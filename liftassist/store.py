"""Separate facility, journey and assistance ledgers."""
import sqlite3
from pathlib import Path

DDL="""
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS source_file(id INTEGER PRIMARY KEY,command TEXT NOT NULL,sha TEXT NOT NULL UNIQUE,raw BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS event(id TEXT PRIMARY KEY,kind TEXT NOT NULL,sha TEXT NOT NULL,raw BLOB NOT NULL,recorded_at TEXT NOT NULL,source_id INTEGER REFERENCES source_file(id));
CREATE TABLE IF NOT EXISTS lift(id TEXT PRIMARY KEY,station TEXT NOT NULL,width_cm INTEGER NOT NULL,capacity INTEGER NOT NULL,revision INTEGER NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fault(id TEXT PRIMARY KEY,lift_id TEXT NOT NULL,opened_at TEXT NOT NULL,closed_at TEXT,witness TEXT,sha TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS retest(id TEXT PRIMARY KEY,lift_id TEXT NOT NULL,at TEXT NOT NULL,load_kg INTEGER NOT NULL,inspector TEXT NOT NULL,passed INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS journey(id TEXT PRIMARY KEY,passenger TEXT NOT NULL,lift_id TEXT NOT NULL,entrance TEXT NOT NULL,platform TEXT NOT NULL,wheelchair_width INTEGER NOT NULL,requested_at TEXT NOT NULL,need_by TEXT NOT NULL,route_minutes INTEGER NOT NULL,staff_id TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS route_segment(id INTEGER PRIMARY KEY,journey_id TEXT NOT NULL,position INTEGER NOT NULL,lift_id TEXT NOT NULL,from_node TEXT NOT NULL,to_node TEXT NOT NULL,minutes INTEGER NOT NULL,UNIQUE(journey_id,position));
CREATE TABLE IF NOT EXISTS route(id INTEGER PRIMARY KEY,journey_id TEXT NOT NULL,revision INTEGER NOT NULL,lift_revision INTEGER NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,event_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS staff_qualification(id TEXT PRIMARY KEY,staff_id TEXT NOT NULL,role TEXT NOT NULL,issued_at TEXT NOT NULL,expires_at TEXT NOT NULL,revoked_at TEXT);
CREATE TABLE IF NOT EXISTS staff_shift(id TEXT PRIMARY KEY,staff_id TEXT NOT NULL,station TEXT NOT NULL,starts TEXT NOT NULL,ends TEXT NOT NULL,state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assistance(id TEXT PRIMARY KEY,journey_id TEXT NOT NULL,staff_id TEXT NOT NULL,arrived_at TEXT NOT NULL,delivered_at TEXT NOT NULL,witness TEXT,passenger_confirmed INTEGER NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS obligation(id INTEGER PRIMARY KEY,journey_id TEXT NOT NULL,kind TEXT NOT NULL,reason TEXT NOT NULL,resolved INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS compensation_contract(id TEXT NOT NULL,revision INTEGER NOT NULL,effective_from TEXT NOT NULL,rate_per_5min_cents INTEGER NOT NULL,max_cents INTEGER NOT NULL,sha TEXT NOT NULL,PRIMARY KEY(id,revision));
CREATE TABLE IF NOT EXISTS compensation(id INTEGER PRIMARY KEY,journey_id TEXT NOT NULL,assist_id TEXT NOT NULL,contract_id TEXT,contract_revision INTEGER,delay_minutes INTEGER NOT NULL,amount_cents INTEGER NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS egress_edge(id TEXT PRIMARY KEY,station TEXT NOT NULL,from_node TEXT NOT NULL,to_node TEXT NOT NULL,capacity_per_min INTEGER NOT NULL,travel_min INTEGER NOT NULL,accessible INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS evac_zone(id TEXT PRIMARY KEY,station TEXT NOT NULL,node TEXT NOT NULL,occupants INTEGER NOT NULL,observed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS smoke_reading(id TEXT PRIMARY KEY,station TEXT NOT NULL,node TEXT NOT NULL,opacity_pct INTEGER NOT NULL,sampled_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fire_door(id TEXT PRIMARY KEY,edge_id TEXT NOT NULL,state TEXT NOT NULL,observed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS access_command(id TEXT PRIMARY KEY,station TEXT NOT NULL,door_id TEXT NOT NULL,edge_id TEXT NOT NULL,state TEXT NOT NULL,issued_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS access_receipt(id TEXT PRIMARY KEY,door_id TEXT NOT NULL,state TEXT NOT NULL,observed_at TEXT NOT NULL,command_id TEXT);
CREATE TABLE IF NOT EXISTS fan_reading(id TEXT PRIMARY KEY,station TEXT NOT NULL,node TEXT NOT NULL,running INTEGER NOT NULL,observed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS movement_reading(id TEXT PRIMARY KEY,station TEXT NOT NULL,edge_id TEXT NOT NULL,batch_id TEXT NOT NULL,count INTEGER NOT NULL,direction TEXT NOT NULL,observed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sensor_calibration(id TEXT PRIMARY KEY,reading_id TEXT NOT NULL,lag_min INTEGER NOT NULL,clock_drift_min INTEGER NOT NULL,received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS refuge_area(id TEXT PRIMARY KEY,station TEXT NOT NULL,node TEXT NOT NULL,protection_min INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS evac_plan(id TEXT PRIMARY KEY,station TEXT NOT NULL,zone_id TEXT NOT NULL,plan_type TEXT NOT NULL,issued_at TEXT NOT NULL,horizon_min INTEGER NOT NULL,target_node TEXT,route_edges TEXT NOT NULL,refuge_node TEXT,accessible_occupants INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS plan_order(id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,seq INTEGER NOT NULL,at_minute INTEGER NOT NULL,door_id TEXT,door_state TEXT,edge_id TEXT,release_count INTEGER,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plan_minute(plan_id TEXT NOT NULL,minute INTEGER NOT NULL,node TEXT NOT NULL,occupants INTEGER NOT NULL,in_transit INTEGER NOT NULL,cumulative_safe INTEGER NOT NULL,stranded INTEGER NOT NULL,PRIMARY KEY(plan_id,minute,node));
CREATE TABLE IF NOT EXISTS plan_outcome(plan_id TEXT NOT NULL,metric TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(plan_id,metric));
CREATE TABLE IF NOT EXISTS plan_task(id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,kind TEXT NOT NULL,location TEXT NOT NULL,headcount INTEGER NOT NULL,due_at TEXT,detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exposure_record(id TEXT PRIMARY KEY,plan_id TEXT NOT NULL,edge_id TEXT NOT NULL,batch_id TEXT NOT NULL,count INTEGER NOT NULL,entered_at TEXT NOT NULL,exposed_from TEXT NOT NULL,exposed_to TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decision(id INTEGER PRIMARY KEY,event_id TEXT NOT NULL,system TEXT NOT NULL,object_id TEXT NOT NULL,outcome TEXT NOT NULL,reason TEXT NOT NULL);
"""
def connect(path):
    dest=Path(path);dest.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(dest);db.row_factory=sqlite3.Row;db.executescript(DDL)
    return db

def one(db,sql,args=()):return db.execute(sql,args).fetchone()
def many(db,sql,args=()):return db.execute(sql,args).fetchall()
def log(db,event,system,key,outcome,reason):
    db.execute('INSERT INTO decision(event_id,system,object_id,outcome,reason) VALUES(?,?,?,?,?)',(event,system,key,outcome,reason))
