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
CREATE TABLE IF NOT EXISTS mobility_asset(id TEXT PRIMARY KEY,station TEXT NOT NULL,width_cm INTEGER NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mobility_reservation(id TEXT PRIMARY KEY,asset_id TEXT NOT NULL,journey_id TEXT NOT NULL,needed_at TEXT NOT NULL,state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mobility_loan(id TEXT PRIMARY KEY,reservation_id TEXT NOT NULL,loaned_at TEXT NOT NULL,returned_at TEXT,condition TEXT,state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mobility_tag(id TEXT PRIMARY KEY,asset_id TEXT NOT NULL,epc TEXT NOT NULL,installed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rfid_scan(id TEXT PRIMARY KEY,epc TEXT NOT NULL,reader TEXT NOT NULL,observed_at TEXT NOT NULL,rssi INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS custody_receipt(id TEXT PRIMARY KEY,asset_id TEXT NOT NULL,holder TEXT NOT NULL,location TEXT NOT NULL,observed_at TEXT NOT NULL,witness TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS asset_frame(asset_id TEXT PRIMARY KEY,frame_serial TEXT NOT NULL UNIQUE,enrolled_at TEXT NOT NULL,certificate TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reader_position(reader TEXT PRIMARY KEY,location TEXT NOT NULL,station TEXT NOT NULL,registered_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tag_certificate(id TEXT PRIMARY KEY,epc TEXT NOT NULL,asset_id TEXT NOT NULL,action TEXT NOT NULL,actor TEXT NOT NULL,effective_at TEXT NOT NULL,certificate TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS frame_witness(id TEXT PRIMARY KEY,asset_id TEXT NOT NULL,frame_serial TEXT NOT NULL,location TEXT NOT NULL,observed_at TEXT NOT NULL,observer TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS location_observation(id TEXT PRIMARY KEY,location TEXT NOT NULL,observed_at TEXT NOT NULL,weight_kg INTEGER NOT NULL,silhouette TEXT NOT NULL,observer TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS custody_hold(id TEXT PRIMARY KEY,asset_id TEXT NOT NULL,location TEXT NOT NULL,ordered_by TEXT NOT NULL,ordered_at TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inventory_check(id TEXT PRIMARY KEY,hold_id TEXT NOT NULL,asset_id TEXT NOT NULL,location TEXT NOT NULL,frame_serial TEXT NOT NULL,checked_at TEXT NOT NULL,clerk TEXT NOT NULL,found INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS asset_transfer(id TEXT PRIMARY KEY,asset_id TEXT NOT NULL,from_station TEXT NOT NULL,to_station TEXT NOT NULL,carrier TEXT NOT NULL,authorized_by TEXT NOT NULL,authorized_at TEXT NOT NULL,due_by TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transfer_receipt(id TEXT PRIMARY KEY,transfer_id TEXT NOT NULL,asset_id TEXT NOT NULL,frame_serial TEXT NOT NULL,location TEXT NOT NULL,received_at TEXT NOT NULL,receiver TEXT NOT NULL,seal_intact INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS carrier_claim(id TEXT PRIMARY KEY,transfer_id TEXT NOT NULL,claimed_at TEXT NOT NULL,claimant TEXT NOT NULL,state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS asset_conflict(id INTEGER PRIMARY KEY,asset_id TEXT NOT NULL,kind TEXT NOT NULL,location TEXT NOT NULL,observed_at TEXT NOT NULL,detail TEXT NOT NULL,state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS asset_custody(asset_id TEXT PRIMARY KEY,holder TEXT NOT NULL,location TEXT NOT NULL,physical_state TEXT NOT NULL,reason TEXT NOT NULL,tag_epc TEXT NOT NULL,tag_location TEXT NOT NULL,tag_state TEXT NOT NULL,updated_by TEXT NOT NULL);
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
