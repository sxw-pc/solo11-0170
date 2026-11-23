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
CREATE TABLE IF NOT EXISTS microphone(id TEXT PRIMARY KEY,station TEXT NOT NULL,x_m INTEGER NOT NULL,y_m INTEGER NOT NULL,calibration_id TEXT NOT NULL,saturation_db REAL);
CREATE TABLE IF NOT EXISTS acoustic_trace(id TEXT PRIMARY KEY,microphone_id TEXT NOT NULL,started_at TEXT NOT NULL,sample_rate_hz INTEGER NOT NULL,bands_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fan_run(id TEXT PRIMARY KEY,station TEXT NOT NULL,fan_id TEXT NOT NULL,started_at TEXT NOT NULL,stopped_at TEXT NOT NULL,speed_pct INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS train_pass(id TEXT PRIMARY KEY,station TEXT NOT NULL,train_id TEXT NOT NULL,passed_at TEXT NOT NULL,speed_kph INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS station_acoustics(station TEXT PRIMARY KEY,reverb_time_s REAL NOT NULL,fan_x_m REAL NOT NULL,fan_y_m REAL NOT NULL,track_x_m REAL NOT NULL,track_y_m REAL NOT NULL,min_airflow_pct INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS noise_complaint(id TEXT PRIMARY KEY,station TEXT NOT NULL,microphone_id TEXT NOT NULL,trace_id TEXT NOT NULL,band_hz INTEGER NOT NULL,started_at TEXT NOT NULL,ended_at TEXT NOT NULL,level_db REAL NOT NULL,saturated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS interim_measure(id TEXT PRIMARY KEY,station TEXT NOT NULL,party TEXT NOT NULL,measure TEXT NOT NULL,issued_at TEXT NOT NULL,expires_at TEXT NOT NULL,status TEXT NOT NULL,basis TEXT NOT NULL,executed_at TEXT,cancelled_at TEXT);
CREATE TABLE IF NOT EXISTS control_experiment(id TEXT PRIMARY KEY,station TEXT NOT NULL,design TEXT NOT NULL,started_at TEXT NOT NULL,ended_at TEXT NOT NULL,fan_speed_pct INTEGER,microphone_id TEXT,trace_id TEXT,band_hz INTEGER,status TEXT NOT NULL);
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
