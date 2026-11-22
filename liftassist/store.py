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
CREATE TABLE IF NOT EXISTS platform_profile(id TEXT PRIMARY KEY,station TEXT NOT NULL,track TEXT NOT NULL,edge_points_json TEXT NOT NULL,survey_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS train_door(id TEXT PRIMARY KEY,train_model TEXT NOT NULL,car_no INTEGER NOT NULL,door_x_cm INTEGER NOT NULL,floor_height_cm INTEGER NOT NULL,door_width_cm INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS bridge_board(id TEXT PRIMARY KEY,station TEXT NOT NULL,length_cm INTEGER NOT NULL,width_cm INTEGER NOT NULL,max_slope_permille INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS dock_measurement(id TEXT PRIMARY KEY,profile_id TEXT NOT NULL,door_id TEXT NOT NULL,stop_offset_cm INTEGER NOT NULL,gap_cm INTEGER NOT NULL,height_diff_cm INTEGER NOT NULL,measured_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fit_survey(id TEXT PRIMARY KEY,profile_id TEXT NOT NULL,survey_version INTEGER NOT NULL,edge_points_json TEXT NOT NULL,horizontal_precision_cm REAL NOT NULL,vertical_precision_cm REAL NOT NULL,yaw_margin_cm REAL NOT NULL,superseded_at TEXT,conflict_state TEXT NOT NULL,event_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS door_load_profile(id TEXT PRIMARY KEY,door_id TEXT NOT NULL,floor_height_cm REAL NOT NULL,load_variation_cm REAL NOT NULL,event_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS board_calibration(id TEXT PRIMARY KEY,board_id TEXT NOT NULL,bearing_end_cm REAL NOT NULL,flex_cm REAL NOT NULL,event_id TEXT NOT NULL,superseded_at TEXT);
CREATE TABLE IF NOT EXISTS stop_control(id TEXT PRIMARY KEY,station TEXT NOT NULL,track TEXT NOT NULL,window_half_width_cm REAL NOT NULL,method TEXT NOT NULL,active_from TEXT NOT NULL,state TEXT NOT NULL,event_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fit_certificate(id TEXT PRIMARY KEY,station TEXT NOT NULL,profile_id TEXT NOT NULL,train_model TEXT NOT NULL,door_id TEXT NOT NULL,board_id TEXT NOT NULL,survey_id TEXT NOT NULL,calibration_id TEXT NOT NULL,requested_half_width_cm REAL NOT NULL,mode TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,required_precision_cm,event_id TEXT NOT NULL,revoked_at TEXT,revoked_reason TEXT);
CREATE TABLE IF NOT EXISTS safe_stop_interval(id INTEGER PRIMARY KEY,certificate_id TEXT NOT NULL,position INTEGER NOT NULL,lower_cm REAL NOT NULL,upper_cm REAL NOT NULL);
CREATE TABLE IF NOT EXISTS fit_counterexample(id INTEGER PRIMARY KEY,certificate_id TEXT NOT NULL,position INTEGER NOT NULL,stop_offset_cm REAL NOT NULL,failed_check TEXT NOT NULL,gap_cm REAL NOT NULL,height_diff_cm REAL NOT NULL,effective_bearing_cm REAL NOT NULL,slope_permille REAL NOT NULL);
CREATE TABLE IF NOT EXISTS fit_action(id TEXT PRIMARY KEY,certificate_id TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,detail TEXT NOT NULL,event_id TEXT NOT NULL,closed_at TEXT);
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
