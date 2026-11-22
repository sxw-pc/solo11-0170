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
CREATE TABLE IF NOT EXISTS display_screen(id TEXT PRIMARY KEY,station TEXT NOT NULL,zone TEXT NOT NULL,device_serial TEXT NOT NULL,mirror_of TEXT);
CREATE TABLE IF NOT EXISTS ad_campaign(id TEXT PRIMARY KEY,advertiser TEXT NOT NULL,asset_hash TEXT NOT NULL,guaranteed_seconds INTEGER NOT NULL,valid_from TEXT NOT NULL,valid_to TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS play_command(id TEXT PRIMARY KEY,screen_id TEXT NOT NULL,campaign_id TEXT NOT NULL,starts TEXT NOT NULL,ends TEXT NOT NULL,priority INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS screen_ack(id TEXT PRIMARY KEY,screen_id TEXT NOT NULL,command_id TEXT NOT NULL,asset_hash TEXT NOT NULL,observed_at TEXT NOT NULL,state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS camera_frame(id TEXT PRIMARY KEY,screen_id TEXT NOT NULL,image_hash TEXT NOT NULL,captured_at TEXT NOT NULL,visible_asset_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ad_contract(id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,product TEXT NOT NULL,guaranteed_seconds INTEGER NOT NULL,required_samples INTEGER NOT NULL,rate_per_second_cents INTEGER NOT NULL,sample_amount_cents INTEGER NOT NULL,signed_at TEXT NOT NULL,sha TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS screen_switch(id TEXT PRIMARY KEY,screen_id TEXT NOT NULL,seq INTEGER NOT NULL,at TEXT NOT NULL,to_asset_hash TEXT NOT NULL,UNIQUE(screen_id,seq));
CREATE TABLE IF NOT EXISTS av_segment(id TEXT PRIMARY KEY,screen_id TEXT NOT NULL,starts TEXT NOT NULL,ends TEXT NOT NULL,visible_asset_hash TEXT NOT NULL,media_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS camera_calibration(id TEXT PRIMARY KEY,screen_id TEXT NOT NULL,at TEXT NOT NULL,offset_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS screen_power(id TEXT PRIMARY KEY,screen_id TEXT NOT NULL,at TEXT NOT NULL,state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS safety_override(id TEXT PRIMARY KEY,screen_id TEXT NOT NULL,starts TEXT NOT NULL,ends TEXT NOT NULL,priority INTEGER NOT NULL,category TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS playback_confirmation(receipt_id TEXT PRIMARY KEY,command_id TEXT NOT NULL,confirmed_seconds INTEGER NOT NULL,confirmed_at TEXT NOT NULL,confirmer TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS player_counter(id TEXT PRIMARY KEY,screen_id TEXT NOT NULL,asset_hash TEXT NOT NULL,at TEXT NOT NULL,total_seconds INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS fulfillment(contract_id TEXT PRIMARY KEY,campaign_id TEXT NOT NULL,product TEXT NOT NULL,proven_seconds INTEGER NOT NULL,max_seconds INTEGER NOT NULL,unproven_seconds INTEGER NOT NULL,samples INTEGER NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,event_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ad_bill(id INTEGER PRIMARY KEY,contract_id TEXT NOT NULL,kind TEXT NOT NULL,seconds INTEGER NOT NULL,samples INTEGER NOT NULL,amount_cents INTEGER NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,event_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS makeup(id INTEGER PRIMARY KEY,contract_id TEXT NOT NULL,delta_seconds INTEGER NOT NULL,owed_seconds INTEGER NOT NULL,window_start TEXT,window_end TEXT,reason TEXT NOT NULL,event_id TEXT NOT NULL);
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
