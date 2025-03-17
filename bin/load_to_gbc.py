#!/usr/bin/env python3
import os
import sys
import time
import argparse
import json

import requests
import globalbiodata as gbc

from google.cloud.sql.connector import Connector
import pymysql
import sqlalchemy as db


parser = argparse.ArgumentParser(description='Load data to GBC')
parser.add_argument('--json', type=str, help='Path to JSON file with data', required=True)
parser.add_argument('--accession-types', type=str, help='Path to JSON file with accession types', required=True)
parser.add_argument('--summary', type=str, help='Path for summary file output', required=True)

parser.add_argument('--db', type=str, help='Database to use (format: instance_name/db_name)', required=True)
parser.add_argument('--dbcreds', type=str, help='Path to JSON file with SQL credentials')
parser.add_argument('--sqluser', type=str, help='SQL user', default=os.environ.get("CLOUD_SQL_USER"))
parser.add_argument('--sqlpass', type=str, help='SQL password', default=os.environ.get("CLOUD_SQL_PASSWORD"))

parser.add_argument('--debug', action='store_true', help='Debug mode')
# parser.add_argument('--dry-run', action='store_true', help='Dry run mode')

args = parser.parse_args()

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

epmc_base_url = "https://www.ebi.ac.uk/europepmc/webservices/rest"

def epmc_data_links(pubmed_id):
    url = f"{epmc_base_url}/MED/{pubmed_id}/datalinks"
    request_params = {
        'format': 'json', 'obtainedBy': 'tm_accession'
    }

    datalinks = query_europepmc(url, request_params)

    hitcount = datalinks.get('hitCount')
    # print(f"Expecting {hitcount} data links")

    links = []
    for link_category in datalinks.get('dataLinkList', {}).get('Category', []):
        for link in link_category.get('Section', {})[0].get('Linklist', {}).get('Link', []):
            target = link.get('Target', {})
            if target.get('Type', {}).get('Name') == 'dataset':
                identifier = target.get('Identifier', {})
                links.append({
                    'accession':identifier.get('ID'), 'db':identifier.get('IDScheme'),
                    'url': identifier.get('IDURL'), 'metadata': {
                        'ObtainedBy': link.get('ObtainedBy'),
                        'publicationDate': link.get('PublicationDate')
                    }
                })

    if len(links) != hitcount:
        raise(f"Error: Incomplete data links - expected {hitcount}, got {len(links)}")

    return links

max_retries = 3
def query_europepmc(endpoint, request_params, retry_count=0):
    response = requests.get(endpoint, params=request_params)
    if response.status_code == 200:
        data = response.json()

    # Handle malformed/incomplete results - retry up to max_retries times
    if not data.get('hitCount'):
        sys.stderr.write(f"Error: No data found for {endpoint} / {request_params}. Retrying...\n")
        if retry_count < max_retries:
            return query_europepmc(endpoint, request_params, retry_count=retry_count+1)
        else:
            sys.exit(f"Error: No data found for {endpoint} / {request_params} after {max_retries} retries\n")

    # Handle empty results
    if data['hitCount'] == 0:
        sys.stderr.write(f"Error: No data found for {request_params}\n")
        return {}

    return data

# lazy loading for GBC resources
def get_gbc_resource(dbname):
    name_mapping = {
        'Electron Microscopy Data Bank': 'emdb',
        'IGSR/1000 Genomes': 'igsr', 'Complex Portal': 'complexportal',
        'European Genome-Phenome Archive': 'ega',
        'ClinicalTrials.gov': 'nct', 'EU Clinical Trials Register': 'eudract',
        'MGnify': 'metagenomics',
    }
    mapped_dbname = dbname.lower() if accession_types.get(dbname.lower()) else name_mapping.get(dbname)

    if isinstance(accession_types.get(mapped_dbname), int):
        gbc_resource = gbc.fetch_resource({'id': accession_types.get(mapped_dbname)}, expanded=False, engine=cloud_engine)
        accession_types[mapped_dbname] = gbc_resource
        if not gbc_resource:
            raise ValueError(f"Error: No GBC resource found for {dbname} (-> {mapped_dbname})")
        return gbc_resource
    else:
        try:
            return accession_types[mapped_dbname]
        except KeyError:
            raise KeyError(f"Error: No GBC resource found for {dbname} (-> {mapped_dbname})")


# import dictionary of indexed accessions, mapped to GBC database resources
accession_types = json.load(open(args.accession_types, 'r'))
epmc_fields = [
    'pmid', 'pmcid', 'title', 'authorList', 'authorString', 'journalInfo', 'grantsList',
    'keywordList', 'meshHeadingList', 'citedByCount', 'hasTMAccessionNumbers'
]
skip_types = ['DOI']

prediction = gbc.Prediction({
    'name': 'EuropePMC text-mined accession loading',
    'date': '2025-02-17',
    'user': 'carlac'
})
prediction.write(engine=cloud_engine, debug=args.debug)

publications = json.load(open(args.json, 'r'))
summary_out = open(args.summary, 'w')

skipped_count = 0
max_time, min_time = 0, 0
for pub in publications.get('results'):
    summary_out.write("---------------------------------------------------------------\n")
    summary_out.write(f"📖 {pub.get('title')} (PMID: {pub.get('pmid')})\n")

    if not pub.get('pmid') or pub.get('hasTMAccessionNumbers') == 'N':
        summary_out.write(f"🚫 Skipping publication without {'PMID' if not pub.get('pmid') else 'TMAccessionNumbers'}\n\n")
        skipped_count += 1
        continue

    t0 = time.time()
    gbc_pub = gbc.fetch_publication({'pubmed_id': pub.get('pmid')}, engine=cloud_engine, debug=args.debug, expanded=False)
    t1 = time.time()

    if not gbc_pub:
        t0 = time.time()
        gbc_pub = gbc.new_publication_from_EuropePMC_result(pub, google_maps_api_key=os.environ.get('GOOGLE_MAPS_API_KEY'))
        t1 = time.time()
        gbc_pub.write(engine=cloud_engine, debug=args.debug)
        t2 = time.time()

    t2 = time.time()
    dblinks_info = epmc_data_links(pub.get('pmid'))
    t3 = time.time()

    summary_out.write(f"1. Creation of gbc.Publication object: {round(t1-t0, 3)}s\n")
    summary_out.write(f"2. Writing publication to database: {round(t2-t1, 3)}s\n")
    summary_out.write(f"3. Fetching {len(dblinks_info)} data links from EuropePMC: {round(t3-t2, 3)}s\n")

    current_links = gbc.select_from_table('accession_publication', {'publication_id': gbc_pub.id}, engine=cloud_engine, debug=args.debug)

    if len(current_links) != len(dblinks_info):
        link_types = {}
        for dblink in dblinks_info:
            if dblink.get('db') in skip_types:
                continue

            gbc_db = get_gbc_resource(dblink.get('db'))
            dblink['gbc_db'] = gbc_db
            dblink['gbc_db_str'] = gbc_db.__str__()

            # pprint(dblink)

            gbc_acc = gbc.Accession({
                'accession': dblink.get('accession'), 'resource': gbc_db,
                'url': dblink.get('url'), 'prediction_metadata': dblink.get('metadata'),
                'prediction': prediction, 'publications': [gbc_pub]
            })

            # print(gbc_acc)
            gbc_acc.write(engine=cloud_engine, debug=args.debug)
            link_types[gbc_db.short_name] = link_types.get(gbc_db.short_name, 0) + 1

        summary_out.write(f"🔗 New data links:{link_types}\n")
    else:
        summary_out.write(f"\n🔗 No new data links to add - already {len(current_links)} in the db\n")

    t4 = time.time()
    summary_out.write(f"4. Writing {len(dblinks_info)} accessions to database: {round(t4-t3, 3)}s\n\n")
    this_time = t4-t0
    max_time = max(max_time or this_time, this_time)
    min_time = min(min_time or this_time, this_time)

summary_out.write("\n")
summary_out.write("📊 Summary of data loading:\n")
summary_out.write(f"📉 Total number of publications skipped: {skipped_count}\n")
summary_out.write(f"📈 Total number of publications loaded: {len(publications.get('results')) - skipped_count}\n")
summary_out.write(f"🕓 Maximum time taken for a publication: {round(max_time, 3)}s\n")
summary_out.write(f"🕓 Minimum time taken for a publication: {round(min_time, 3)}s\n")
summary_out.close()