#!/usr/bin/env python3
import sys
import os

import requests
import json
import time
import random
import argparse

from google.cloud.sql.connector import Connector
import pymysql
import sqlalchemy as db

parser = argparse.ArgumentParser(description='Query EuropePMC for accession data.')
# parser.add_argument('--cursor-file', type=str, help='File to read/write cursor mark', required=True)
parser.add_argument('--accession-types', type=str, help='Path to JSON file with accession types', required=True)
parser.add_argument('--outdir', type=str, help='Output directory for results', required=True)

parser.add_argument('--db', type=str, help='Database to use (format: instance_name/db_name)', required=True)
parser.add_argument('--dbcreds', type=str, help='Path to JSON file with SQL credentials')
parser.add_argument('--sqluser', type=str, help='SQL user', default=os.environ.get("CLOUD_SQL_USER"))
parser.add_argument('--sqlpass', type=str, help='SQL password', default=os.environ.get("CLOUD_SQL_PASSWORD"))

parser.add_argument('--page-size', type=int, default=1000, help='Number of results per page')
parser.add_argument('--limit', type=int, default=0, help='Limit the number of results')

args = parser.parse_args()
limit = args.limit if args.limit > 0 else None

if not os.path.exists(args.outdir):
    os.makedirs(args.outdir)

# setup SQL connection
sqluser, sqlpass = None, None
if args.dbcreds:
    creds = json.load(open(args.dbcreds, 'r'))
    sqluser, sqlpass = creds.get('user'), creds.get('pass')
elif args.sqluser and args.sqlpass:
    sqluser, sqlpass = args.sqluser, args.sqlpass
else:
    sys.exit("Error: No SQL credentials provided")

gcp_connector = Connector()
instance, db_name = args.db.split('/')
def getcloudconn() -> pymysql.connections.Connection:
    conn: pymysql.connections.Connection = gcp_connector.connect(
        instance, "pymysql",
        user=sqluser,
        password=sqlpass,
        db=db_name
    )
    return conn

cloud_engine = db.create_engine("mysql+pymysql://", creator=getcloudconn, pool_recycle=60 * 5, pool_pre_ping=True)
cloud_conn = cloud_engine.connect()

max_retries = 10
def query_europepmc(endpoint, request_params, retry_count=0, graceful_exit=False):
    response = requests.get(endpoint, params=request_params)
    if response.status_code == 200:
        data = response.json()

    # Handle malformed/incomplete results - retry up to max_retries times
    if not data.get('hitCount'):
        sys.stderr.write(f"Error: No data found for {endpoint} / {request_params}. Retrying...\n")
        if retry_count < max_retries:
            time.sleep(random.randint(1, 15))
            return query_europepmc(endpoint, request_params, retry_count=retry_count+1)
        else:
            sys.stderr.write(f"Error: No data found for {endpoint} / {request_params} after {max_retries} retries\n")
            sys.exit(0) if graceful_exit else sys.exit(1)

    # Handle empty results
    if data['hitCount'] == 0:
        sys.stderr.write(f"Error: No data found for {request_params}\n")
        return {}

    return data

# hash directory for results files (to avoid overpopulating a single directory)
def generate_json_file(c):
    cstr = str(c).zfill(7)
    hashdir = '/'.join(list(cstr)[::-1][:4])
    outdir = f"{args.outdir}/{hashdir}"
    if not os.path.exists(outdir):
        os.makedirs(outdir)
    return f"{outdir}/results.{cstr}.json"

# manually create dictionary of indexed accessions, mapped to GBC database resources
accession_types = json.load(open(args.accession_types, 'r'))
acc_query = "(%s)" % ' OR '.join([f"ACCESSION_TYPE:{at}" for at in accession_types])
epmc_fields = [
    'pmid', 'pmcid', 'title', 'authorList', 'authorString', 'journalInfo', 'grantsList',
    'keywordList', 'meshHeadingList', 'citedByCount', 'hasTMAccessionNumbers'
]

db_cursors = cloud_conn.execute(db.text("SELECT cursor_mark, cursor_id FROM tmp_cursor_tracking ORDER BY time DESC LIMIT 1")).fetchone()
if db_cursors:
    cursor, c = db_cursors
    c = int(c)
else:
    cursor, c = None, 1

epmc_base_url = "https://www.ebi.ac.uk/europepmc/webservices/rest"
more_data = True
while more_data:
    search_params = {
        'query': acc_query, 'resultType': 'core',
        'format': 'json', 'pageSize': args.page_size,
        'cursorMark': cursor
    }

    # if error, exit gracefully : this allows us to resume from where we left off in the
    # event of an error (using the last cursor mark in the database)
    data = query_europepmc(f"{epmc_base_url}/search", search_params, graceful_exit=True)

    limit = limit or data.get('hitCount')
    if cursor is None:
        print(f"--- Expecting {limit} of {data.get('hitCount')} results!")

    formatted_results = {'cursor': cursor, 'results': []}
    for result in data['resultList']['result']:
        formatted_results['results'].append({k: result[k] for k in epmc_fields if k in result})

    with(open(generate_json_file(c), 'w')) as f:
        json.dump(formatted_results, f, indent=4)
        print(f"---- Wrote {args.page_size} results to file {f.name} (cursor: {cursor})")

    c += 1
    limit -= args.page_size
    print(f"----- Remaining results: {limit}")

    cursor = data.get('nextCursorMark')
    if not cursor or limit <= 0:
        more_data = False
    else:
        cloud_conn.execute(db.text(f"INSERT INTO tmp_cursor_tracking (cursor_mark, cursor_id) VALUES ('{cursor}', {c})"))
        cloud_conn.commit()