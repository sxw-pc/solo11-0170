"""Event-timed smoke/crowd coupled evacuation rehearsal with audit trail.

The evacuation ledger in :mod:`liftassist.evacuation` only *captures* edge,
zone, smoke and fire-door evidence; it never derives an evacuation time
limit.  This module replays that evidence inside a minute-stepped,
time-expanded directed network:

* a fire-door order changes both the pedestrian edges that can be walked and
  the pressure-driven smoke boundary (an open door lets smoke advance along
  its edge, a closed door blocks it), so safe capacity evolves with the
  orders already issued — it is not a fixed capacity-per-minute figure;
* a batch may only enter an edge when the whole walking interval can be
  proven safe with the evidence knowable at that minute.  Entry opacity
  alone never clears an edge;
* sensor lag/clock drift, duplicate movement counts and unexecuted door
  orders are resolved into safe upper/lower bounds from evidence available
  at the time, never from future knowledge;
* exposure is append-only: a late smoke reading may change later orders and
  annotate the original plan's actual exposure window, but people already
  recorded in smoke can never be rewritten as "never departed".

Only two plan kinds are pre-approved by the fire procedure: ``shelter``
(sealed same-floor refuge with rescue handoff and refuge timing) and
``stairs`` (phased egress via an independent smoke-free stair).  The
``direct`` kind exists so a rehearsal can demonstrate why forcing people
through a smoke-coupled door fails where a static max-flow answer passes.
"""
from datetime import datetime,timedelta
import heapq,json,math
from .store import one,many,log

SMOKE_THRESHOLD_PCT=10
SMOKE_TRAVEL_PER_EDGE_MIN=1


def _parse(ts):
    return datetime.fromisoformat(str(ts).replace('Z','+00:00'))


def _fmt(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _minute(ts,epoch):
    return int(round((_parse(ts)-epoch).total_seconds()/60))


def _iso(epoch,minute):
    return _fmt(epoch+timedelta(minutes=minute))


def static_throughput_estimate(db,station,zone_id,route_edges=None):
    """The naive answer: static graph, capacity treated as fixed per minute.

    Ignores doors as anything but on/off switches and ignores smoke
    feedback entirely.  Returned for comparison with the coupled rehearsal;
    it is never the basis of an evacuation order.
    """
    z=one(db,'SELECT * FROM evac_zone WHERE id=? AND station=?',(zone_id,station))
    if not z:raise ValueError('unknown_evac_zone')
    rows=many(db,'SELECT * FROM egress_edge WHERE station=?',(station,))
    if route_edges is not None:
        by_id={r['id']:r for r in rows}
        missing=[x for x in route_edges if x not in by_id]
        if missing:raise ValueError('unknown_route_edge:'+','.join(missing))
        edges=[by_id[x] for x in route_edges]
    else:
        edges=list(rows)
    if not edges:return {'zone_id':zone_id,'occupants':z['occupants'],'route_edges':[],
                         'minutes_to_clear':None,'all_clear_claim':False,'reason':'no_egress_edge'}
    bottleneck=min(r['capacity_per_min'] for r in edges)
    total_travel=sum(r['travel_min'] for r in edges)
    occupants=z['occupants']
    minutes=math.ceil(occupants/bottleneck)+total_travel if bottleneck>0 else None
    return {'zone_id':zone_id,'occupants':occupants,
            'route_edges':[r['id'] for r in edges],
            'bottleneck_per_min':bottleneck,'total_travel_min':total_travel,
            'minutes_to_clear':minutes,
            'all_clear_claim':minutes is not None,
            'reason':'static_max_flow_ignores_door_smoke_feedback'}


# ---------------------------------------------------------------- evidence --

def door_order(db,e):
    if e['state'] not in ('open','closed'):raise ValueError('invalid_door_state')
    if not one(db,'SELECT 1 FROM egress_edge WHERE station=? AND id=?',(e['station'],e['edge_id'])):
        raise ValueError('unknown_egress_edge')
    db.execute('INSERT INTO access_command VALUES(?,?,?,?,?,?)',
               (e['order_id'],e['station'],e['door_id'],e['edge_id'],e['state'],e['issued_at']))
    log(db,e['id'],'evac_plan',e['order_id'],'captured','access_command')
    return 'captured'


def door_receipt(db,e):
    cmd=one(db,'SELECT * FROM access_command WHERE id=?',(e.get('command_id'),)) if e.get('command_id') else None
    if e.get('command_id') and not cmd:raise ValueError('unknown_access_command')
    db.execute('INSERT INTO access_receipt VALUES(?,?,?,?,?)',
               (e['receipt_id'],e['door_id'],e['state'],e['observed_at'],e.get('command_id')))
    log(db,e['id'],'evac_plan',e['receipt_id'],'captured','access_receipt')
    return 'captured'


def fan(db,e):
    if e['running'] not in (0,1,True,False):raise ValueError('invalid_fan_state')
    db.execute('INSERT INTO fan_reading VALUES(?,?,?,?,?)',
               (e['reading_id'],e['station'],e['node'],int(bool(e['running'])),e['observed_at']))
    log(db,e['id'],'evac_plan',e['reading_id'],'captured','fan_reading')
    return 'captured'


def movement(db,e):
    if e['count']<0:raise ValueError('invalid_movement_count')
    if e['direction'] not in ('with_edge','against_edge'):raise ValueError('invalid_movement_direction')
    if not one(db,'SELECT 1 FROM egress_edge WHERE station=? AND id=?',(e['station'],e['edge_id'])):
        raise ValueError('unknown_egress_edge')
    db.execute('INSERT INTO movement_reading VALUES(?,?,?,?,?,?,?)',
               (e['reading_id'],e['station'],e['edge_id'],e['batch_id'],e['count'],e['direction'],e['observed_at']))
    log(db,e['id'],'evac_plan',e['reading_id'],'captured','movement_reading')
    return 'captured'


def calibration(db,e):
    if e['lag_min']<0 or e['clock_drift_min']<0:raise ValueError('invalid_sensor_calibration')
    if not one(db,'SELECT 1 FROM smoke_reading WHERE id=?',(e['reading_id'],)):
        raise ValueError('unknown_smoke_reading')
    db.execute('INSERT INTO sensor_calibration VALUES(?,?,?,?,?)',
               (e['calibration_id'],e['reading_id'],e['lag_min'],e['clock_drift_min'],e['received_at']))
    log(db,e['id'],'evac_plan',e['calibration_id'],'captured','sensor_calibration')
    return 'captured'


def refuge(db,e):
    if e['protection_min']<=0:raise ValueError('invalid_refuge_protection')
    db.execute('INSERT INTO refuge_area VALUES(?,?,?,?)',
               (e['refuge_id'],e['station'],e['node'],e['protection_min']))
    log(db,e['id'],'evac_plan',e['refuge_id'],'captured','refuge_area')
    return 'captured'


# ----------------------------------------------------------------- rehearsal --

class _Rehearsal:
    """Minute-stepped coupled simulation over one station's evidence."""

    def __init__(self,db,station,epoch,horizon_min,route_edges,threshold_pct=SMOKE_THRESHOLD_PCT):
        self.db=db
        self.station=station
        self.epoch=epoch
        self.horizon=horizon_min
        self.threshold=threshold_pct
        self.edges={r['id']:r for r in many(db,'SELECT * FROM egress_edge WHERE station=?',(station,))}
        missing=[x for x in route_edges if x not in self.edges]
        if missing:raise ValueError('unknown_route_edge:'+','.join(missing))
        self.route=list(route_edges)
        self.nodes={n for r in self.edges.values() for n in (r['from_node'],r['to_node'])}
        self.zones=[dict(r) for r in many(db,'SELECT * FROM evac_zone WHERE station=?',(station,))]
        self.zones.sort(key=lambda r:r['id'])
        self.doors=[dict(r) for r in many(db,
            'SELECT d.*,e.id AS eid FROM fire_door d JOIN egress_edge e ON e.id=d.edge_id WHERE e.station=?',(station,))]
        for r in self.doors:r['at']=_minute(r['observed_at'],epoch)
        self.commands=[dict(r) for r in many(db,'SELECT * FROM access_command WHERE station=?',(station,))]
        for r in self.commands:r['at']=_minute(r['issued_at'],epoch)
        self.receipts=[dict(r) for r in many(db,
            'SELECT r.*,c.station FROM access_receipt r LEFT JOIN access_command c ON c.id=r.command_id WHERE c.station=? OR c.station IS NULL',(station,))]
        for r in self.receipts:r['at']=_minute(r['observed_at'],epoch)
        # edges that actually carry a fire door; other edges are open passages
        self.doored={r['eid'] for r in self.doors}
        self.doored.update(r['edge_id'] for r in self.commands)
        self.fans=[dict(r) for r in many(db,'SELECT * FROM fan_reading WHERE station=?',(station,))]
        for r in self.fans:r['at']=_minute(r['observed_at'],epoch)
        self.smoke=[dict(r) for r in many(db,'SELECT * FROM smoke_reading WHERE station=?',(station,))]
        for r in self.smoke:r['sampled_min']=_minute(r['sampled_at'],epoch)
        self.calib={r['reading_id']:dict(r) for r in many(db,'SELECT * FROM sensor_calibration')}
        self.movements=[dict(r) for r in many(db,'SELECT * FROM movement_reading WHERE station=?',(station,))]
        for r in self.movements:r['at']=_minute(r['observed_at'],epoch)
        # orders issued by the plan itself are assumed to execute on time;
        # external access_command rows are only intents until a receipt confirms
        self.assumed={}
        self.adj={n:[] for n in self.nodes}
        for r in self.edges.values():
            self.adj.setdefault(r['from_node'],[]).append((r['to_node'],r['id']))
            self.adj.setdefault(r['to_node'],[]).append((r['from_node'],r['id']))
        self.orders=[];self.seq=0
        self.exposures=[]
        self.tasks=[]

    # -- evidence bounds --------------------------------------------------

    def _sensor_window(self,reading):
        """Pessimistic/optimistic minute when a reading's smoke was real."""
        c=self.calib.get(reading['id'])
        lag=c['lag_min'] if c else 0
        drift=c['clock_drift_min'] if c else 0
        received=_minute(c['received_at'],self.epoch) if c else reading['sampled_min']
        sampled=reading['sampled_min']
        pess=min(sampled-lag-drift,received-lag-drift)
        opt=max(sampled-lag+drift,received)
        return pess,opt,received

    def _door_state(self,edge_id,at):
        """Confirmed door state: ledger observations and receipts only.

        A bare access_command with no receipt is an unexecuted intent and
        never changes the confirmed state.  Orders from the plan being run
        are injected through ``assumed`` because the brigade is being told
        to execute them as part of the plan.
        """
        # an injected plan decision is the scenario under test and wins;
        # otherwise only ledger observations/receipts confirm door state
        if edge_id in self.assumed:return self.assumed[edge_id]
        states=[]
        for r in self.doors:
            if r['eid']==edge_id and r['at']<=at:states.append((r['at'],r['state']))
        for r in self.receipts:
            linked=one(self.db,'SELECT edge_id FROM access_command WHERE id=?',(r['command_id'],)) if r['command_id'] else None
            if linked and linked['edge_id']==edge_id and r['at']<=at:states.append((r['at'],r['state']))
        if states:return sorted(states)[-1][1]
        # no door evidence at all: ordinary open passage, but a door whose
        # state is unknown still cannot be proven either way
        return 'open' if edge_id not in self.doored else None

    def _fan_cleared(self,at):
        cleared=set()
        for r in self.fans:
            if r['at']<=at and r['running']:cleared.add(r['node'])
        return cleared

    def smoke_bounds(self,at):
        """Earliest smoke arrival per node, knowable by minute ``at``.

        Two Dijkstra maps on the undirected station graph with edge weight
        one minute per edge:

        * ``pess`` — safety map.  A reading counts as soon as threshold
          smoke could have been present (lag + clock drift, earliest bound),
          so a door that cannot be proven closed blocks the traversal;
        * ``opt`` — optimistic map using latest plausible presence; used
          only for exposure annotation after the fact.

        A reading at or below ``SMOKE_THRESHOLD_PCT`` is sub-threshold
        evidence: retained in the ledger but never seeded as smoke.
        """
        seeds_p,seeds_o=set(),set()
        for r in self.smoke:
            pess,opt,received=self._sensor_window(r)
            if received>at:continue
            if r['opacity_pct']>self.threshold:
                seeds_p.add((r['node'],pess));seeds_o.add((r['node'],opt))
        return self._dijkstra(seeds_p,at,pessimistic=True),self._dijkstra(seeds_o,at,pessimistic=False)

    def _dijkstra(self,seeds,at,pessimistic):
        dist={n:math.inf for n in self.nodes}
        heap=[]
        for node,when in seeds:
            if node not in dist:continue
            if when<dist[node]:dist[node]=when;heapq.heappush(heap,(when,node))
        while heap:
            d,u=heapq.heappop(heap)
            if d!=dist[u]:continue
            for v,edge_id in self.adj.get(u,()):
                state=self._door_state(edge_id,at)
                if state=='closed':continue
                if pessimistic and state is None:continue  # cannot prove sealed
                nd=d+SMOKE_TRAVEL_PER_EDGE_MIN
                if nd<dist[v]:dist[v]=nd;heapq.heappush(heap,(nd,v))
        # a confirmed running exhaust fan keeps its node below threshold
        for node in self._fan_cleared(at):
            if node in dist:dist[node]=math.inf
        return dist

    def edge_certificate(self,edge_id,entry_min,smoke_p):
        """Whole-interval proof required before a batch may enter."""
        edge=self.edges[edge_id]
        state=self._door_state(edge_id,entry_min)
        if state!='open':
            return False,'door_not_confirmed_open' if state is None else 'fire_door_closed'
        travel=edge['travel_min']
        for node in (edge['from_node'],edge['to_node']):
            arrival=smoke_p.get(node,math.inf)
            # strict boundary: smoke arriving exactly at the walk-out minute
            # means the interval cannot be proven safe (cut-off convention)
            if arrival<entry_min+travel:
                return False,'smoke_cannot_be_excluded_for_full_walk'
        return True,'safe_for_full_walk'

    # -- orders ------------------------------------------------------------

    def add_order(self,plan_id,seq,at_minute,reason,door_id=None,door_state=None,
                  edge_id=None,release_count=None):
        oid=f'{plan_id}-O{seq}'
        self.orders.append(oid)
        self.db.execute('INSERT INTO plan_order VALUES(?,?,?,?,?,?,?,?,?)',
                        (oid,plan_id,seq,at_minute,door_id,door_state,edge_id,release_count,reason))

    # -- simulation --------------------------------------------------------

    def run(self,plan_id,plan_type,target_node,refuge_node,accessible_occupants):
        occupancy={z['node']:z['occupants'] for z in self.zones}
        in_transit=[]  # (arrive_minute, edge_id, batch_id, count, start_node, end_node)
        cumulative_safe=0;exposed_out=0;exposed_batches=set()
        trajectories=[]
        for minute in range(self.horizon+1):
            smoke_p,_=self.smoke_bounds(minute)
            # arrivals first
            remaining=[]
            for arrival,edge_id,batch_id,count,a,b in in_transit:
                if arrival<=minute:
                    if b in occupancy:occupancy[b]+=count
                    else:occupancy[b]=count;self.nodes.add(b)
                    if b==target_node:
                        cumulative_safe+=count
                        if batch_id in exposed_batches:exposed_out+=count
                else:remaining.append((arrival,edge_id,batch_id,count,a,b))
            in_transit=remaining
            if minute>=self.horizon:break
            if plan_type=='shelter':
                # single same-floor move, the refuge edge capacity still binds
                edge_id=self.route[0]
                edge=self.edges[edge_id]
                start=edge['from_node'];waiting=occupancy.get(start,0)
                ok,why=self.edge_certificate(edge_id,minute,smoke_p)
                if waiting>0 and ok:
                    n=min(waiting,edge['capacity_per_min'])
                    occupancy[start]-=n
                    in_transit.append((minute+edge['travel_min'],edge_id,f'B{minute}',n,start,edge['to_node']))
                    self.seq+=1;self.add_order(plan_id,self.seq,minute,'move_to_sealed_refuge',
                                          edge_id=edge_id,release_count=n)
            else:
                # stairs or direct: every waiting node may feed its route
                # edge, but only when the whole walking interval is provably
                # safe with the evidence knowable at this minute
                for edge_id in self.route:
                    edge=self.edges[edge_id]
                    a=edge['from_node'];waiting_here=occupancy.get(a,0)
                    if waiting_here<=0:continue
                    ok,why=self.edge_certificate(edge_id,minute,smoke_p)
                    if ok:
                        n=min(waiting_here,edge['capacity_per_min'])
                        occupancy[a]-=n
                        in_transit.append((minute+edge['travel_min'],edge_id,
                                           f'{a}-B{minute}',n,a,edge['to_node']))
                        self.seq+=1;self.add_order(plan_id,self.seq,minute,'release_to_egress',
                                              edge_id=edge_id,release_count=n)
                    elif plan_type=='direct':
                        # rehearsal of the unsafe conventional response: the
                        # static-flow instinct forces the next batch through
                        n=min(waiting_here,edge['capacity_per_min'])
                        occupancy[a]-=n
                        in_transit.append((minute+edge['travel_min'],edge_id,
                                           f'{a}-B{minute}',n,a,edge['to_node']))
                        self.seq+=1;self.add_order(plan_id,self.seq,minute,'forced_without_safety_certificate',
                                              edge_id=edge_id,release_count=n)
                        exposed_batches.add(f'{a}-B{minute}')
                        self._record_exposure(plan_id,edge_id,f'{a}-B{minute}',n,minute,smoke_p,
                                              reason='released_without_full_walk_safety_proof')
            on_route=sum(x[3] for x in in_transit)
            stranded=sum(v for k,v in occupancy.items() if k!=target_node and k!=refuge_node)
            for node in sorted(occupancy):
                self.db.execute('INSERT INTO plan_minute VALUES(?,?,?,?,?,?,?)',
                                (plan_id,minute,node,occupancy[node],on_route,cumulative_safe,stranded))
            trajectories.append({'minute':minute,'occupancy':dict(occupancy),
                                 'in_transit':on_route,'cumulative_safe':cumulative_safe,
                                 'stranded':stranded})
        total=sum(z['occupants'] for z in self.zones)
        at_refuge=occupancy.get(refuge_node,0) if refuge_node else 0
        at_target=occupancy.get(target_node,0) if target_node else 0
        safe_unexposed=cumulative_safe-exposed_out
        stranded=total-safe_unexposed-at_refuge
        return {'trajectories':trajectories,'cumulative_safe':safe_unexposed,
                'exposed_out':exposed_out,
                'at_refuge':at_refuge,'at_target':at_target,'stranded':stranded,
                'total_occupants':total,'accessible_occupants':accessible_occupants,
                'final_occupancy':occupancy}

    def _record_exposure(self,plan_id,edge_id,batch_id,count,entry_minute,smoke_p,reason):
        eid=f'{plan_id}-X{len(self.exposures)+1}'
        edge=self.edges[edge_id]
        smoke_from=smoke_p.get(edge['from_node'],math.inf)
        smoke_to=smoke_p.get(edge['to_node'],math.inf)
        exposed_from=max(entry_minute,min(smoke_from,smoke_to))
        exposed_to=entry_minute+edge['travel_min']
        self.exposures.append(eid)
        self.db.execute('INSERT INTO exposure_record VALUES(?,?,?,?,?,?,?,?,?)',
                        (eid,plan_id,edge_id,batch_id,count,_iso(self.epoch,entry_minute),
                         _iso(self.epoch,exposed_from) if math.isfinite(exposed_from) else '',
                         _iso(self.epoch,exposed_to),reason))

    def reconcile_movement(self):
        """Independent flow readings bound who actually entered each edge.

        Duplicate counts for the same edge/batch/timestamp are collapsed;
        readings against the edge direction only establish a zero lower
        bound (they cannot add people to the evacuation flow).
        """
        seen=set();bounds=[]
        for r in sorted(self.movements,key=lambda x:(x['at'],x['id'])):
            key=(r['edge_id'],r['batch_id'],r['observed_at'])
            if key in seen:continue  # duplicate counting: keep first evidence
            seen.add(key)
            sign=1 if r['direction']=='with_edge' else -1
            bounds.append({'edge_id':r['edge_id'],'batch_id':r['batch_id'],
                           'count_low':r['count'] if sign>0 else 0,
                           'count_high':r['count'],'at_minute':r['at']})
        return bounds


def activate(db,e):
    """Issue one of the two pre-approved plans (or a ``direct`` rehearsal)."""
    if e['plan_type'] not in ('shelter','stairs','direct'):raise ValueError('unknown_evac_plan_type')
    if e['horizon_min']<=0:raise ValueError('invalid_plan_horizon')
    z=one(db,'SELECT * FROM evac_zone WHERE id=? AND station=?',(e['zone_id'],e['station']))
    if not z:raise ValueError('unknown_evac_zone')
    route=e['route_edges']
    if not route:raise ValueError('route_edges_required')
    accessible=int(e.get('accessible_occupants',0))
    if accessible<0 or accessible>z['occupants']:raise ValueError('invalid_accessible_occupants')
    target=e.get('target_node');refuge=e.get('refuge_node')
    if e['plan_type']=='shelter':
        if not refuge:raise ValueError('shelter_requires_refuge_node')
        r=one(db,'SELECT * FROM refuge_area WHERE station=? AND node=?',(e['station'],refuge))
        if not r:raise ValueError('unknown_refuge_area')
    if e['plan_type']=='stairs' and not target:raise ValueError('stairs_requires_target_node')
    epoch=_parse(e['issued_at'])
    sim=_Rehearsal(db,e['station'],epoch,e['horizon_min'],route)
    # Required door state for EVERY fire door in the station:
    #  - shelter: all doors sealed, people move through the open refuge edge;
    #  - stairs: route doors opened, every other door kept sealed so smoke
    #    cannot reach the independent stair;
    #  - direct: the route door is opened (which itself moves the smoke
    #    boundary), other doors sealed.
    required={}
    for edge_id in sorted(sim.doored):
        if e['plan_type']=='shelter':required[edge_id]='closed'
        elif e['plan_type']=='stairs':required[edge_id]='open' if edge_id in route else 'closed'
        else:required[edge_id]='open' if edge_id==route[0] else 'closed'
    first=sim.edges[route[0]]
    cmd_n=0
    for edge_id,want_state in required.items():
        if sim._door_state(edge_id,0)==want_state:continue
        door=one(db,"SELECT * FROM fire_door WHERE edge_id=? ORDER BY observed_at DESC LIMIT 1",(edge_id,))
        door_id=door['id'] if door else f'FD-{edge_id}'
        sim.seq+=1;sim.add_order(e['plan_id'],sim.seq,0,f'plan_requires_door_{want_state}',
                             door_id=door_id,door_state=want_state,edge_id=edge_id)
        cmd_n+=1
        db.execute('INSERT INTO access_command VALUES(?,?,?,?,?,?)',
                   (f'{e["plan_id"]}-CMD{cmd_n}',e['station'],door_id,edge_id,want_state,e['issued_at']))
        # the plan assumes the brigade executes its own orders on time;
        # an external command without a receipt would not count this way
        sim.assumed[edge_id]=want_state
    result=sim.run(e['plan_id'],e['plan_type'],target,refuge,accessible)
    db.execute('INSERT INTO evac_plan VALUES(?,?,?,?,?,?,?,?,?,?)',
               (e['plan_id'],e['station'],e['zone_id'],e['plan_type'],e['issued_at'],
                e['horizon_min'],target,json.dumps(route),refuge,accessible))
    metrics={'total_occupants':result['total_occupants'],
             'safe_out':result['cumulative_safe'],
             'exposed_out':result['exposed_out'],
             'sheltered':result['at_refuge'],
             'rescue_stranded':result['stranded'],
             'orders_issued':len(sim.orders)}
    for k,v in metrics.items():
        db.execute('INSERT INTO plan_outcome VALUES(?,?,?)',(e['plan_id'],k,str(v)))
    if e['plan_type']=='shelter':
        prot=one(db,'SELECT protection_min FROM refuge_area WHERE node=?',(refuge,))['protection_min']
        due=_fmt(epoch+timedelta(minutes=prot))
        db.execute('INSERT INTO plan_task VALUES(?,?,?,?,?,?,?)',
                   (f'{e["plan_id"]}-T1',e['plan_id'],'rescue_handoff',refuge,result['at_refuge'],due,
                    f'smoke_refuge_protection_{prot}_min'))
        db.execute('INSERT INTO plan_task VALUES(?,?,?,?,?,?,?)',
                   (f'{e["plan_id"]}-T2',e['plan_id'],'refuge_timer',refuge,result['at_refuge'],due,
                    'evacuate_before_refuge_protection_expires'))
    if e['plan_type']=='stairs':
        db.execute('INSERT INTO plan_task VALUES(?,?,?,?,?,?,?)',
                   (f'{e["plan_id"]}-T1',e['plan_id'],'stairhead_guidance',first['from_node'],
                    result['total_occupants'],None,'phase_batches_at_capacity_per_min'))
        db.execute('INSERT INTO plan_task VALUES(?,?,?,?,?,?,?)',
                   (f'{e["plan_id"]}-T2',e['plan_id'],'accessible_transfer',first['from_node'],
                    accessible,None,'independent_smoke_free_stair_accessible_route'))
    if e['plan_type']=='direct' and result['stranded']:
        db.execute('INSERT INTO plan_task VALUES(?,?,?,?,?,?,?)',
                   (f'{e["plan_id"]}-T1',e['plan_id'],'rescue_handoff',first['from_node'],
                    result['stranded'],None,'stranded_after_unsafe_forced_egress'))
    log(db,e['id'],'evac_plan',e['plan_id'],'activated',f'{e["plan_type"]}_coupled_rehearsal')
    return {'plan_id':e['plan_id'],'plan_type':e['plan_type'],**metrics,
            'static_estimate':static_throughput_estimate(db,e['station'],e['zone_id'],route)}


def review(db,e):
    """Re-judge a plan when late evidence arrives.

    Already-announced orders, recorded passages and fire-brigade receipts
    stay untouched.  New exposure rows may be appended; existing rows are
    never rewritten — nobody already in smoke becomes "not departed".
    """
    p=one(db,'SELECT * FROM evac_plan WHERE id=?',(e['plan_id'],))
    if not p:raise ValueError('unknown_evac_plan')
    epoch=_parse(p['issued_at'])
    horizon=p['horizon_min']
    sim=_Rehearsal(db,p['station'],epoch,horizon,json.loads(p['route_edges']))
    # review runs now: every reading received so far is admissible evidence
    received=[sim._sensor_window(r)[2] for r in sim.smoke]
    evidence_at=max([horizon]+received)
    smoke_p,_=sim.smoke_bounds(evidence_at)
    known={(r['plan_id'],r['edge_id'],r['batch_id'])
           for r in many(db,'SELECT plan_id,edge_id,batch_id FROM exposure_record WHERE plan_id=?',(e['plan_id'],))}
    existing=len(known);added=0;move_keys=set()
    # movement evidence says who really entered each edge; late smoke bounds
    # annotate the actual exposure window of those batches.  Duplicate
    # counting (same edge/batch/timestamp reported twice) is ignored.
    for r in sim.movements:
        mkey=(r['edge_id'],r['batch_id'],r['observed_at'])
        if mkey in move_keys:continue
        move_keys.add(mkey)
        key=(e['plan_id'],r['edge_id'],r['batch_id'])
        if key in known:continue
        edge=sim.edges[r['edge_id']]
        smoke_from=smoke_p.get(edge['from_node'],math.inf)
        smoke_to=smoke_p.get(edge['to_node'],math.inf)
        if min(smoke_from,smoke_to)<r['at']+edge['travel_min']:
            xid=f'{e["plan_id"]}-X{existing+added+1}'
            exposed_from=max(r['at'],min(smoke_from,smoke_to))
            db.execute('INSERT INTO exposure_record VALUES(?,?,?,?,?,?,?,?,?)',
                       (xid,e['plan_id'],r['edge_id'],r['batch_id'],r['count'],
                        _iso(epoch,r['at']),_iso(epoch,exposed_from),
                        _iso(epoch,r['at']+edge['travel_min']),'late_smoke_evidence_annotates_actual_exposure'))
            added+=1
            known.add(key)
    log(db,e['id'],'evac_plan',e['plan_id'],'reviewed',f'late_evidence_exposure_rows_added={added}')
    return {'plan_id':e['plan_id'],'exposure_rows_added':added}
