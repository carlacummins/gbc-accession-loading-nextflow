import sys
import re
from datetime import datetime

import sqlalchemy as db
from sqlalchemy.dialects.mysql import insert # for on_duplicate_key_update

from pprint import pprint
import locationtagger
import googlemaps

# ---------------------------------------------------------------------------- #
# Classes for Global Biodata Core Resource data                                #
# ---------------------------------------------------------------------------- #

def extract_fields_by_type(d, t):
    extracted = {}
    for k, v in d.items():
        if re.match(f"^{t}", k):
            extracted[re.sub(f"^{t}_", "", k)] = v
    return extracted

class URL:
    """
    URL and associated information

    `id`: Database ID for URL
    `url`: URL
    `url_country`: Country of URL
    `url_coordinates`: Coordinates of URL
    `status`: ConnectionStatus object(s)
    `wayback_url`: URL for Wayback Machine
    """
    id:int
    url:str
    url_country:str
    url_coordinates:str
    url_status:str
    status:list
    wayback_url:str

    def __init__(self, u):
        self.id = u.get('id')
        self.url = u.get('url')
        self.url_country = u.get('url_country')
        self.url_coordinates = u.get('url_coordinates')
        self.wayback_url = u.get('wayback_url')

        # ConnectionStatus can either come as a list of dicts, a list of ConnectionStatus objects
        # or status fields directly in the url object.
        # End result should be a list of ConnectionStatus objs.
        if not u.get('status') or len(u.get('status')) == 0:
            cs = []
        elif type(u.get('status')) == 'list':
            if type(u.get('status')[0]) == 'dict':
                cs = [ConnectionStatus(s) for s in u.get('status')]
            elif type(u.get('status')[0]) == 'ConnectionStatus':
                cs = u.get('status')
        else:
            cs = [ConnectionStatus({'url_id':self.id, 'status':u.get('url_status'), 'date':u.get('connection_date')})]
        self.status = cs

    def __str__(self):
        url_str = ', '.join([
            f"id={self.id}", f"url={self.url}", f"url_country={self.url_country}",
            f"url_coordinates={self.url_coordinates}", f"wayback_url={self.wayback_url}",
            f"status=[{', '.join([s.__str__() for s in self.status])}]" if self.status else "status=[]"
        ])
        return f"URL({url_str})"

    def write(self, conn=None, engine=None, debug=False):
        conn_statuses = self.status
        d = self.__dict__
        del d['status']
        new_url_id = insert_into_table('url', d, conn=conn, engine=engine, debug=debug)
        self.id = new_url_id
        for c in conn_statuses:
            c.url_id = self.id
            c.write(conn=conn, engine=engine, debug=debug)
        self.status = conn_statuses
        return self.id

    def delete(self, conn=None, engine=None, debug=False):
        if conn is None:
            conn = engine.connect()

        if not self.id:
            raise ValueError("URL object must have an ID to delete.")

        delete_from_table('connection_status', {'url_id':self.id}, conn=conn, engine=engine, debug=debug)
        del_result = delete_from_table('url', {'id':self.id}, conn=conn, engine=engine, debug=debug)
        return del_result

class Prediction:
    """
    Prediction information

    `id` : Database ID for Prediction
    `name` : Name of prediction pipeline/type
    `date` : Date of run
    `user` : User who ran prediction pipeline
    `additional_metadata` : Additional prediction data in JSON format
    """
    id:int
    name:str
    date:str
    user:str
    additional_metadata:dict

    def __init__(self, p):
        self.id = p.get('id')
        self.name = p.get('prediction_name') or p.get('name')
        self.date = p.get('prediction_date') or p.get('date')
        self.user = p.get('prediction_user') or p.get('user')
        self.additional_metadata = p.get('additional_prediction_metadata') or p.get('additional_metadata')

    def __str__(self):
        pred_str = f"Prediction(id={self.id}, name={self.name}, date={self.date}, user={self.user}, additional_metadata={self.additional_metadata})"
        return pred_str

    def write(self, conn=None, engine=None, debug=False):
        new_prediction_id = insert_into_table('prediction', self.__dict__, conn=conn, engine=engine, debug=debug)
        self.id = new_prediction_id
        return self.id

    def delete(self, conn=None, engine=None, debug=False):
        if conn is None:
            conn = engine.connect()

        if not self.id:
            raise ValueError("Prediction object must have an ID to delete.")

        del_result = delete_from_table('prediction', {'id':self.id}, conn=conn, engine=engine, debug=debug)
        return del_result

class Resource:
    """
    Biodata Resource description

    `id`: Database ID for resource
    `short_name`: Short name of resource
    `common_name`: Common name of resource
    `full_name`: Full name of resource
    `url`: URL object
    `prediction`: Prediction object
    `prediction_metadata`: Additional prediction metadata in JSON format
    `publications`: Publication object(s)
    `grants`: Grant object(s)
    `is_gcbr`: boolean value describing Core Biodata Resource status
    `is_latest`: boolean value describing whether this is the most current version of the resource
    """
    id:int
    short_name:str
    common_name:str
    full_name:str
    url:URL
    prediction:Prediction
    prediction_metadata:str
    publications:list
    grants:list
    is_gcbr:bool
    is_latest:bool

    def __init__(self, r):
        self.id = r.get('id')
        self.short_name = r.get('short_name')
        self.common_name = r.get('common_name')
        self.full_name = r.get('full_name')

        r2 = {k:r[k] for k in r.keys() if k!='id'} # copy input and remove id to avoid propagating it to other objects
        self.url = URL(r2) if type(r.get('url')) == str else r.get('url')
        self.prediction = r.get('prediction') or Prediction(r2)
        self.prediction_metadata = r.get('resource_prediction_metadata') or r.get('prediction_metadata')
        self.is_gcbr = r.get('is_gcbr')
        self.is_latest = r.get('is_latest')

        if r.get('grants'):
            self.grants = [Grant(r2)] if type(r.get('grants')[0]) == str else r.get('grants')
        elif r.get('ext_grant_ids') and r.get('grant_agencies'):
            self.grants = [Grant({'ext_grant_id':g, 'grant_agency':ga}) for g, ga in zip(r.get('ext_grant_ids').split(','), r.get('grant_agencies').split(','))]
        else:
            self.grants = []

        if not r.get('publications') and r.get('title') and r.get('pubmed_id') and r.get('authors'):
            self.publications = [Publication(r2)]
        else:
            self.publications = r.get('publications')

    def __str__(self):
        resource_str = ', '.join([
            f"id={self.id}", f"short_name={self.short_name}", f"common_name={self.common_name}", f"full_name={self.full_name}",
            f"is_gcbr={self.is_gcbr}", f"is_latest={self.is_latest}", f"url={self.url.__str__()}",
            f"prediction={self.prediction.__str__()}", f"prediction_metadata={self.prediction_metadata}",
            f"publications=[{', '.join([p.__str__() for p in self.publications])}]" if self.publications else "publications=[]",
            f"grants=[{', '.join(g.__str__() for g in self.grants)}]" if self.grants else "grants=[]"
        ])
        return f"Resource({resource_str})"

    def write(self, conn=None, engine=None, debug=False, force=False):
        if not self.url.id or force:
            url_id = self.url.write(conn=conn, engine=engine, debug=debug)
            self.url.id = url_id

        if not self.prediction.id or force:
            prediction_id = self.prediction.write(conn=conn, engine=engine, debug=debug)
            self.prediction.id = prediction_id

        # set is_latest to 0 for other versions of this resource
        if self.is_latest:
            lconn = engine.connect()
            lconn.execute(db.text(f"UPDATE resource SET is_latest = 0 WHERE short_name = '{self.short_name}'"))
            lconn.commit()

        resource_cols = {
            'id':self.id, 'short_name':self.short_name, 'common_name':self.common_name, 'full_name':self.full_name,
            'url_id':self.url.id, 'prediction_id':self.prediction.id, 'prediction_metadata':self.prediction_metadata,
            'is_gcbr':self.is_gcbr, 'is_latest':self.is_latest
        }
        new_resource_id = insert_into_table('resource', resource_cols, conn=conn, engine=engine, debug=debug)
        self.id = new_resource_id

        if self.publications:
            # delete_from_table('resource_publication', {'resource_id':new_resource_id}, conn=conn, engine=engine, debug=debug) # delete existing links
            for p in self.publications:
                if not p.id or force:
                    new_pub_id = p.write(conn=conn, engine=engine, debug=debug)
                    p.id = new_pub_id
                # create links between resource and publication tables
                insert_into_table('resource_publication', {'resource_id':new_resource_id, 'publication_id':p.id}, conn=conn, engine=engine, debug=debug)

        if self.grants:
            # delete_from_table('resource_grant', {'resource_id':new_resource_id}, conn=conn, engine=engine, debug=debug) # delete existing links
            for g in self.grants:
                if not g.id or force:
                    new_grant_id = g.write(conn=conn, engine=engine, debug=debug)
                    g.id = new_grant_id
                # create links between resource and grant tables
                insert_into_table('resource_grant', {'resource_id':new_resource_id, 'grant_id':g.id}, conn=conn, engine=engine, debug=debug)

        return self.id

    def delete(self, conn=None, engine=None, debug=False):
        if conn is None:
            conn = engine.connect()

        if not self.id:
            raise ValueError("Resource object must have an ID to delete.")

        rp_del = delete_from_table('resource_publication', {'resource_id':self.id}, conn=conn, engine=engine, debug=debug)
        rg_del = delete_from_table('resource_grant', {'resource_id':self.id}, conn=conn, engine=engine, debug=debug)
        u_del = self.url.delete(conn=conn, engine=engine, debug=debug)
        r_result = delete_from_table('resource', {'id':self.id}, conn=conn, engine=engine, debug=debug)

        return r_result

class ConnectionStatus:
    """

    `url_id`: Database ID for URL
    `status`: Code returned from connection
    `date`: Date of connection
    `is_online`: boolean describing whether return code indicates resource is online
    `is_latest`: boolean value describing whether this is the most recent connection attempt
    """
    url_id:int
    status:str
    date:str
    is_online:bool
    is_latest:bool

    def __init__(self, c):
        self.url_id = c.get('url_id')
        self.status = str(c.get('status'))
        self.date = c.get('connection_date') or c.get('date')
        self.is_latest = c.get('is_latest', 0)

        if not self.date:
            self.date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.is_latest = 1

        if c.get('is_online') == None:
            self.is_online = self.status[:18] not in ['404', '500', 'HTTPConnectionPool']
        else:
            self.is_online = c.get('is_online')

    def __str__(self):
        status_str = f"ConnectionStatus(url_id={self.url_id}, status={self.status}, date={self.date}, is_online={self.is_online}, is_latest={self.is_latest})"
        return status_str

    def write(self, conn=None, engine=None, debug=False):
        # update is_latest to 0 for other connection statuses
        if self.is_latest:
            lconn = engine.connect()
            lconn.execute(db.text(f"UPDATE connection_status SET is_latest = 0 WHERE url_id = {self.url_id}"))
            lconn.commit()

        insert_into_table('connection_status', self.__dict__, conn=conn, engine=engine, debug=debug)

    def delete(self, conn=None, engine=None, debug=False):
        if conn is None:
            conn = engine.connect()

        if not self.url_id:
            raise ValueError("ConnectionStatus object must have a URL ID to delete.")

        del_result = delete_from_table('connection_status', {'url_id':self.url_id, 'date':self.date}, conn=conn, engine=engine, debug=debug)
        return del_result

class Publication:
    """

    `id`: Database ID for Publication
    `title`: Title of publication
    `pubmed_id`: PubMed ID
    `pmc_id`: PubMed Central ID
    `publication_date`: Date of publication
    `authors`: Authors of publication
    `affiliation`: Affiliation of authors
    `affiliation_countries`: Countries of affiliation
    `citation_count`: Number of citations
    `keywords`: Keywords/Mesh terms
    """
    id:int
    title:str
    pubmed_id:int
    pmc_id:str
    publication_date:str
    authors:str
    affiliation:str
    affiliation_countries:str
    grants:list
    citation_count:int
    keywords:str

    def __init__(self, p):
        self.id = p.get('id')
        self.title = p.get('publication_title') or p.get('title')
        self.pubmed_id = p.get('pubmed_id')
        self.pmc_id = p.get('pmc_id')
        self.publication_date = p.get('publication_date')
        self.authors = p.get('authors')
        self.affiliation = p.get('affiliation')
        self.affiliation_countries = p.get('affiliation_countries')
        self.citation_count = p.get('citation_count')
        self.keywords = p.get('keywords')

        if p.get('grants'):
            self.grants = [Grant(p)] if type(p.get('grants')[0]) == str else p.get('grants')
        elif p.get('ext_grant_ids') and p.get('grant_agencies'):
            self.grants = [Grant({'ext_grant_id':g, 'grant_agency':ga}) for g, ga in zip(p.get('ext_grant_ids').split(','), p.get('grant_agencies').split(','))]
        else:
            self.grants = []

    def __str__(self):
        pub_str = ', '.join([
            f"id={self.id}", f"title={self.title}", f"pubmed_id={self.pubmed_id}", f"pmc_id={self.pmc_id}",
            f"publication_date={self.publication_date}", f"authors={self.authors}", f"affiliation={self.affiliation}",
            f"affiliation_countries={self.affiliation_countries}",f"citation_count={self.citation_count}",
            f"keywords={self.keywords}", f"grants=[{', '.join(g.__str__() for g in self.grants)}]",
            f"grants=[{', '.join(g.__str__() for g in self.grants)}]" if self.grants else "grants=[]"
        ])
        return f"Publication({pub_str})"

    def write(self, conn=None, engine=None, debug=False, force=False):
        self_dict = self.__dict__.copy()
        pub_grants = self_dict.pop('grants')
        new_pub_id = insert_into_table('publication', self_dict, conn=conn, engine=engine, debug=debug)
        self.id = new_pub_id

        if pub_grants:
            # delete_from_table('publication_grant', {'publication_id':new_pub_id}, conn=conn, engine=engine, debug=debug) # delete existing links
            for g in pub_grants:
                if not g.id or force:
                    new_grant_id = g.write(conn=conn, engine=engine, debug=debug)
                    g.id = new_grant_id

                # create links between publication and grant tables
                insert_into_table('publication_grant', {'publication_id':new_pub_id, 'grant_id':g.id}, conn=conn, engine=engine, debug=debug)

        return self.id

    def delete(self, conn=None, engine=None, debug=False):
        if conn is None:
            conn = engine.connect()

        if not self.id:
            raise ValueError("Publication object must have an ID to delete.")

        del_result = delete_from_table('publication', {'id':self.id}, conn=conn, engine=engine, debug=debug)
        return del_result

class Grant:
    """
    `id`: Database ID for Grant
    `ext_grant_id`: External grant ID
    `grant_agencies`: GrantAgency object(s)
    """
    id:int
    ext_grant_id:str
    grant_agency:str

    def __init__(self, g):
        self.id = g.get('id')
        self.ext_grant_id = g.get('ext_grant_id')

        if type(g.get('grant_agency')) == str:
            self.grant_agency = GrantAgency({'name': g.get('grant_agency')})
        elif type(g.get('grant_agency')) == GrantAgency:
            self.grant_agency = g.get('grant_agency')
        else:
            raise ValueError(f"Grant Agency must be a string or GrantAgency object. Got: {g.get('grant_agency')} (type:{type(g.get('grant_agency'))}).")

    def __str__(self):
        grant_str = f"Grant(id={self.id}, ext_grant_id={self.ext_grant_id}, grant_agency={self.grant_agency.__str__()})"
        return grant_str

    def write(self, conn=None, engine=None, debug=False, force=False):
        if not self.grant_agency.id or force:
            new_ga_id = self.grant_agency.write(conn=conn, engine=engine, debug=debug)
            self.grant_agency.id = new_ga_id

        g_cols = {'id':self.id, 'ext_grant_id':self.ext_grant_id, 'grant_agency_id':self.grant_agency.id}
        new_g_id = insert_into_table('grant', g_cols, conn=conn, engine=engine, debug=debug)
        self.id = new_g_id
        return self.id

    def delete(self, conn=None, engine=None, debug=False):
        if conn is None:
            conn = engine.connect()

        if not self.id:
            raise ValueError("Grant object must have an ID to delete.")

        delete_from_table('resource_grant', {'grant_id':self.id}, conn=conn, engine=engine, debug=debug)
        del_result = delete_from_table('grant', {'id':self.id}, conn=conn, engine=engine, debug=debug)
        return del_result

class GrantAgency:
    """
    `id`: Database ID for GrantAgency
    `name`: Name of grant agency
    `parent_agency`: Parent agency (to show hierarchy of agencies)
    `representative_agency`: Representative agency (to show grouping of agencies)
    """
    id:int
    name:str
    country:str
    parent_agency: 'GrantAgency'
    representative_agency: 'GrantAgency'


    def __init__(self, ga):
        self.id = ga.get('id')
        self.name = ga.get('name')
        self.country = ga.get('country')

        if ga.get('parent_agency') or ga.get('parent_agency_id'):
            self.parent_agency = ga.get('parent_agency') or GrantAgency({'id':ga.get('parent_agency_id')})
        else:
            self.parent_agency = None

        if ga.get('representative_agency') or ga.get('representative_agency_id'):
            self.representative_agency = ga.get('representative_agency') or GrantAgency({'id':ga.get('representative_agency_id')})
        else:
            self.representative_agency = None

    def __str__(self):
        grant_agency_str = f"GrantAgency(id={self.id}, name={self.name}, country={self.country}, "
        grant_agency_str += f"parent_agency_id={self.parent_agency.id if self.parent_agency else ''}"
        grant_agency_str += f"representative_agency_id={self.representative_agency.id if self.representative_agency else ''})"
        return grant_agency_str

    def write(self, conn=None, engine=None, debug=False):
        writable = {'id':self.id, 'name':self.name, 'country':self.country}
        writable['parent_agency_id'] = self.parent_agency.id if self.parent_agency else None
        writable['representative_agency_id'] = self.representative_agency.id if self.representative_agency else None

        new_ga_id = insert_into_table('grant_agency', writable, conn=conn, engine=engine, debug=debug)
        self.id = new_ga_id
        return self.id

    def delete(self, conn=None, engine=None, debug=False):
        if conn is None:
            conn = engine.connect()

        if not self.id:
            raise ValueError("GrantAgency object must have an ID to delete.")

        del_result = delete_from_table('grant_agency', {'id':self.id}, conn=conn, engine=engine, debug=debug)
        return del_result

class Accession:
    """
    `accession`: Accession ID
    `resource`: Resource object
    `publications`: Publication object(s)
    `prediction`: Prediction object
    `url`: URL string
    `prediction_metadata`: Additional prediction metadata in JSON format
    """
    accession:str
    resource:Resource
    publications:list
    prediction:Prediction
    url:str
    prediction_metadata:str

    def __init__(self, a):
        self.accession = a.get('accession')
        self.resource = a.get('resource') or Resource(extract_fields_by_type(a, 'resource'))
        self.publications = a.get('publications') or [Publication(extract_fields_by_type(a, 'publication'))]
        self.prediction = a.get('prediction') or Prediction(extract_fields_by_type(a, 'prediction'))
        self.url = a.get('url')
        self.prediction_metadata = a.get('prediction_metadata')

    def __str__(self):
        accession_str = ', '.join([
            f"accession={self.accession}", f"resource={self.resource.__str__()}",
            f"prediction={self.prediction.__str__()}", f"prediction_metadata={self.prediction_metadata}",
            f"publications=[{', '.join([p.__str__() for p in self.publications])}]" if self.publications else "publications=[]",
            f"url={self.url}", f"prediction_metadata={self.prediction_metadata}"
        ])
        return f"Accession({accession_str})"

    def write(self, conn=None, engine=None, debug=False, force=False):
        if not self.resource.id or force:
            resource_id = self.resource.write(conn=conn, engine=engine, debug=debug)
            self.resource.id = resource_id

        if not self.prediction.id or force:
            prediction_id = self.prediction.write(conn=conn, engine=engine, debug=debug)
            self.prediction.id = prediction_id

        accession_cols = {
            'accession':self.accession, 'resource_id':self.resource.id, 'prediction_id':self.prediction.id,
            'url':self.url, 'prediction_metadata':self.prediction_metadata
        }
        insert_into_table('accession', accession_cols, conn=conn, engine=engine, debug=debug)


        if self.publications:
            for p in self.publications:
                if not p.id or force:
                    new_pub_id = p.write(conn=conn, engine=engine, debug=debug)
                    p.id = new_pub_id

                # create links between resource and publication tables
                insert_into_table('accession_publication', {'accession':self.accession, 'publication_id':p.id}, conn=conn, engine=engine, debug=debug)

    def delete(self, conn=None, engine=None, debug=False):
        if conn is None:
            conn = engine.connect()

        if not self.accession:
            raise ValueError("Accession object must have an accession field to perform delete.")

        ap_del = delete_from_table('accession_publication', {'accession':self.accession}, conn=conn, engine=engine, debug=debug)
        ac_del = delete_from_table('accession', {'accession':self.accession}, conn=conn, engine=engine, debug=debug)

# ---------------------------------------------------------------------------- #
# Database helper methods                                                      #
# ---------------------------------------------------------------------------- #

# manually defined primary and unique keys for tables
table_keys = {
    'resource': {'pk': ['id'], 'uk': ['short_name', 'url_id', 'prediction_id']},
    'url': {'pk': ['id'], 'uk': ['url']},
    'connection_status': {'pk': ['url_id', 'date'], 'uk': []},
    'prediction': {'pk': ['id'], 'uk': ['name', 'date']},
    'publication': {'pk': ['id'], 'uk': ['pubmed_id']},
    'grant': {'pk': ['id'], 'uk': ['ext_grant_id', 'grant_agency_id']},
    'grant_agency': {'pk': ['id'], 'uk': ['name']},
    'resource_publication': {'pk': ['resource_id', 'publication_id'], 'uk': []},
    'resource_grant': {'pk': ['resource_id', 'grant_id'], 'uk': []},
    'publication_grant': {'pk': ['publication_id', 'grant_id'], 'uk': []},
    'accession': {'pk': ['accession'], 'uk': []},
    'accession_publication': {'pk': ['accession', 'publication_id'], 'uk': []},
}

def get_primary_keys(table, conn):
    cached_pks = table_keys.get(table.name, {}).get('pk', None)
    if cached_pks is not None:
        return cached_pks
    return db.inspect(conn).get_pk_constraint(table.name)['constrained_columns']

def get_unique_keys(table, conn):
    cached_uks = table_keys.get(table.name, {}).get('uk', None)
    if cached_uks is not None:
        return cached_uks
    insp_uks = db.inspect(conn).get_unique_constraints(table.name)
    try:
        return insp_uks[0]["column_names"]
    except IndexError:
        return []

def get_all_keys(table, conn):
    return get_primary_keys(table, conn) + get_unique_keys(table, conn)

def fetch_id_from_unique_keys(table, data, conn, debug=False):
    # detect unique keys for table and use to query id
    uniq_col_names = get_unique_keys(table, conn)
    if not uniq_col_names:
        return 0

    wheres = [table.columns.get(ucn) == data[ucn] for ucn in uniq_col_names]
    if debug:
        print(f"--> finding ids with cols: {', '.join(uniq_col_names)}")

    select = db.select(table.c.id).where(db.and_(*wheres))
    result = conn.execute(select).fetchone()
    if result is None:
        raise ValueError(f"Entity not found in table {table.name} with unique keys: ", {k:v for k, v in data.items() if k in uniq_col_names})
    return result[0]

def remove_key_fields(table, conn, data): # also remove empty values
    key_names = get_all_keys(table, conn)
    return {k:v for k, v in data.items() if (k not in key_names and v is not None)}

def stringify_data(data):
    for k, v in data.items():
        if type(v) == list:
            data[k] = '; '.join(v)
    return data

def insert_into_table(table_name, data, conn=None, engine=None, debug=False):
    metadata_obj = db.MetaData()
    table = db.Table(table_name, metadata_obj, autoload_with=engine)
    data = stringify_data(data)

    if debug:
        print(f"\n--> Inserting into table: {table_name}")
        print(data)

    if conn is None:
        conn = engine.connect()

    trans = conn.begin()  # Begin a transaction
    try:
        insert_stmt = insert(table).values(data)
        data_no_pks = remove_key_fields(table, conn, data)
        if data_no_pks: # some tables may not have any non-PK fields
            do_update_stmt = insert_stmt.on_duplicate_key_update(data_no_pks)
            if debug:
                print(f"Updating {table_name} with data: {data_no_pks}")
            result = conn.execute(do_update_stmt)
        else:
            result = conn.execute(insert_stmt.prefix_with('IGNORE'))
        trans.commit()  # Commit the transaction

        inserted_pk = result.inserted_primary_key[0]
        affected_rows = result.rowcount
        # print(f"inserted_pk: {inserted_pk}; affected_rows: {affected_rows}")

        # if (inserted_pk == 0 or (inserted_pk > 0 and affected_rows == 0)):
        if (not inserted_pk or (inserted_pk and affected_rows == 0)):
            # entity already existed and was ignored - find id of entity to return
            existing_id = fetch_id_from_unique_keys(table, data, conn, debug=debug) or inserted_pk
            if debug:
                print(f"Entity already exists. Fetched id: {existing_id}")
            this_id = existing_id
        elif (inserted_pk and affected_rows > 1):
            # entity already existed and was updated
            this_id = inserted_pk
            if debug:
                print(f"Entity already exists. Updated id: {this_id}")
        else:
            new_resource_id = inserted_pk
            if debug:
                print(f"New entity added. Inserted id: {result.inserted_primary_key[0]}")
            this_id = new_resource_id

    except Exception as e:
        trans.rollback()  # Rollback the transaction if an error occurs
        sys.stderr.write(f"Transaction rolled back due to: {e}\n")
        raise
    finally:
        conn.close()  # Close the connection

    return this_id

def delete_from_table(table_name, data, conn=None, engine=None, debug=False):
    metadata_obj = db.MetaData()
    table = db.Table(table_name, metadata_obj, autoload_with=engine)
    data = stringify_data(data)

    if debug:
        print(f"\n--> Deleting from table: {table_name} WHERE:")
        print(' AND '.join([f"{k} == {data[k]}" for k in data.keys()]))

    if conn is None:
        conn = engine.connect()

    trans = conn.begin()  # Begin a transaction
    try:
        wheres = [table.columns.get(c) == data[c] for c in data.keys()]
        del_result = conn.execute(db.delete(table).where(db.and_(*wheres)))
        if debug:
            print(f"Deleted {del_result.rowcount} rows.")
        trans.commit()  # Commit the transaction
    except Exception as e:
        trans.rollback()  # Rollback the transaction if an error occurs
        sys.stderr.write(f"Transaction rolled back due to: {e}\n")
        raise
    finally:
        conn.close()  # Close the connection

    return del_result.rowcount

def select_from_table(table_name, data={}, conn=None, engine=None, debug=False):
    metadata_obj = db.MetaData()
    table = db.Table(table_name, metadata_obj, autoload_with=engine)

    if debug:
        print(f"\n--> Selecting from table: {table_name} WHERE:")
        print(' AND '.join([f"{k} == {data[k]}" for k in data.keys()]))

    if conn is None:
        conn = engine.connect()

    wheres = []
    for c in data.keys():
        if type(data[c]) == list:
            wheres.append(table.columns.get(c).in_(data[c]))
        else:
            wheres.append(table.columns.get(c) == data[c])
    select = db.select(table).where(db.and_(*wheres))
    result = conn.execute(select).fetchall()

    # convert result to list of dicts
    d_result = [dict(zip(table.columns.keys(), list(r))) for r in result]

    conn.close()
    return d_result

# ----------------------------------------------------------------------- #
# Fetcher methods for Global Biodata Resource data                        #
# ----------------------------------------------------------------------- #

def fetch_resource(query, expanded=True, conn=None, engine=None, debug=False):
    resource_raw = select_from_table('resource', query, conn=conn, engine=engine, debug=debug)
    if len(resource_raw) == 0:
        return None

    resources = []
    for r in resource_raw:
        r['url'] = fetch_url({'id':r['url_id']}, conn=conn, engine=engine, debug=debug)
        r['prediction'] = fetch_prediction({'id':r['prediction_id']}, conn=conn, engine=engine, debug=debug)

        if expanded:
            pub_ids = select_from_table('resource_publication', {'resource_id':r['id']}, conn=conn, engine=engine, debug=debug)
            r['publications'] = fetch_publication({'id':[p['publication_id'] for p in pub_ids]}, conn=conn, engine=engine, debug=debug)
            r['publications'] = [r['publications']] if type(r['publications']) != list else r['publications']

            grant_ids = select_from_table('resource_grant', {'resource_id':r['id']}, conn=conn, engine=engine, debug=debug)
            r['grants'] = fetch_grant({'id':[g['grant_id'] for g in grant_ids]}, conn=conn, engine=engine, debug=debug)
            r['grants'] = [r['grants']] if (r['grants'] is not None and type(r['grants']) != list) else r['grants']

        resources.append(Resource(r))

    return resources if len(resources) > 1 else resources[0]

def fetch_all_resources(expanded=True, conn=None, engine=None, debug=False):
    return fetch_resource({}, expanded=expanded, conn=conn, engine=engine, debug=debug)

def fetch_url(query, expanded=True, conn=None, engine=None, debug=False):
    url_raw = select_from_table('url', query, conn=conn, engine=engine, debug=debug)
    if len(url_raw) == 0:
        return None

    urls = []
    for u in url_raw:
        if expanded:
            u['status'] = fetch_connection_status({'url_id':u['id']}, conn=conn, engine=engine, debug=debug)
            u['status'] = [u['status']] if (u['status'] is not None and type(u['status']) != list) else u['status']
        urls.append(URL(u))

    return urls if len(urls) > 1 else urls[0]

def fetch_all_urls(expanded=True, conn=None, engine=None, debug=False):
    return fetch_url({}, expanded=expanded, conn=conn, engine=engine, debug=debug)

def fetch_connection_status(query, conn=None, engine=None, debug=False):
    status_raw = select_from_table('connection_status', query, conn=conn, engine=engine, debug=debug)
    if len(status_raw) == 0:
        return None

    conn_stats = [ConnectionStatus(cs) for cs in status_raw]

    return conn_stats if len(conn_stats) > 1 else conn_stats[0]

def fetch_all_connection_statuses(conn=None, engine=None, debug=False):
    return fetch_connection_status({}, conn=conn, engine=engine, debug=debug)

def fetch_prediction(query, conn=None, engine=None, debug=False):
    prediction_raw = select_from_table('prediction', query, conn=conn, engine=engine, debug=debug)
    if len(prediction_raw) == 0:
        return None

    predictions = [Prediction(p) for p in prediction_raw]

    return predictions if len(predictions) > 1 else predictions[0]

def fetch_all_predictions(conn=None, engine=None, debug=False):
    return fetch_prediction({}, conn=conn, engine=engine, debug=debug)

def fetch_publication(query, expanded=True, conn=None, engine=None, debug=False):
    publication_raw = select_from_table('publication', query, conn=conn, engine=engine, debug=debug)
    if len(publication_raw) == 0:
        return None

    publications = []
    for p in publication_raw:
        grant_ids = select_from_table('publication_grant', {'publication_id':p['id']}, conn=conn, engine=engine, debug=debug)

        if expanded:
            p['grants'] = fetch_grant({'id':[g['grant_id'] for g in grant_ids]}, conn=conn, engine=engine, debug=debug)
            p['grants'] = [p['grants']] if (p['grants'] is not None and type(p['grants']) != list) else p['grants']

        publications.append(Publication(p))

    return publications if len(publications) > 1 else publications[0]

def fetch_all_publications(conn=None, engine=None, debug=False):
    return fetch_publication({}, conn=conn, engine=engine, debug=debug)

def fetch_grant(query, conn=None, engine=None, debug=False):
    grant_raw = select_from_table('grant', query, conn=conn, engine=engine, debug=debug)
    if len(grant_raw) == 0:
        return None

    grants = []
    for g in grant_raw:
        g['grant_agency'] = fetch_grant_agency({'id':g['grant_agency_id']}, conn=conn, engine=engine, debug=debug)
        grants.append(Grant(g))

    return grants if len(grants) > 1 else grants[0]

def fetch_all_grants(conn=None, engine=None, debug=False):
    return fetch_grant({}, conn=conn, engine=engine, debug=debug)

def fetch_grant_agency(query, conn=None, engine=None, debug=False):
    grant_agency_raw = select_from_table('grant_agency', query, conn=conn, engine=engine, debug=debug)
    if len(grant_agency_raw) == 0:
        return None

    grant_agencies = [GrantAgency(ga) for ga in grant_agency_raw]

    return grant_agencies if len(grant_agencies) > 1 else grant_agencies[0]

def fetch_all_grant_agencies(conn=None, engine=None, debug=False):
    return fetch_grant_agency({}, conn=conn, engine=engine, debug=debug)


# ----------------------------------------------------------------------- #
# Other helper methods                                                    #
# ----------------------------------------------------------------------- #
def new_publication_from_EuropePMC_result(epmc_result, google_maps_api_key=None):
    affiliations, countries = _extract_affiliations(epmc_result, google_maps_api_key=google_maps_api_key)
    new_publication = Publication({
        'publication_title': epmc_result.get('title', ''), 'pubmed_id': epmc_result.get('pmid', ''), 'pmc_id': epmc_result.get('pmcid', ''),
        'publication_date': epmc_result.get('journalInfo', {}).get('printPublicationDate'), 'grants': _extract_grants(epmc_result),
        'keywords': '; '.join(_extract_keywords(epmc_result)), 'citation_count': epmc_result.get('citedByCount', 0),
        'authors': epmc_result.get('authorString', ''), 'affiliation': affiliations, 'affiliation_countries': countries
    })
    return new_publication

def _extract_grants(metadata):
    # extract grant list
    try:
        grant_list = metadata['grantsList']['grant']
    except KeyError:
        return []

    grants = []
    for g in grant_list:
        ga = g.get('agency', '')
        grants.append(Grant({'ext_grant_id':g.get('grantId', ''), 'grant_agency':GrantAgency({'name':ga})}))

    return grants

def _extract_keywords(metadata):
    keywords = []

    # first, MeSH terms
    these_mesh_terms = metadata.get('meshHeadingList', {}).get('meshHeading', [])
    for m in these_mesh_terms:
        keywords.append(f"'{m['descriptorName']}'")
        if m.get('meshQualifierList'):
            for q in m.get('meshQualifierList', {}).get('meshQualifier', []):
                keywords.append(f"'{q['qualifierName']}'")

    # then, other keywords
    these_keywords = metadata.get('keywordList', {}).get('keyword', [])
    for k in these_keywords:
        keywords.append(f"'{k}'")

    return keywords

def _clean_affilation(s):
    # format and replace common abbreviations
    s = re.sub(r'\s*[\w\.]+@[\w\.]+\s*', '', s) # remove email addresses & surrounding whitespace
    s = re.sub(r'\s?[A-Z]{1,2}[0-9R][0-9A-Z]? [0-9][A-Z]{2}', '', s) # remove UK postal codes
    s = s.strip()
    s = re.sub(r'USA[\.]?$', 'United States', s)
    s = re.sub(r'UK[\.]?$', 'United Kingdom', s)
    return s

# pull author affiliations and identify countries
def _extract_affiliations(metadata, google_maps_api_key=None):
    # extract author affiliations & countries
    affiliations, countries = [], []
    affiliation_dict, countries_dict = {}, {}
    try:
        author_list = metadata['authorList']['author']
        for author in author_list:
            affiliation_list = author.get('authorAffiliationDetailsList', {}).get('authorAffiliation', [])
            for a in affiliation_list:
                clean_a = _clean_affilation(a['affiliation'])
                affiliation_dict[clean_a] = 1
                a_countries = _find_country(clean_a, google_maps_api_key=google_maps_api_key)
                countries_dict.update({x:1 for x in a_countries[0]})
        affiliations = list(affiliation_dict.keys())
        countries = list(countries_dict.keys())
    except KeyError:
        pass

    return affiliations, countries

def _find_country(s, google_maps_api_key=None):
    # print(f"Searching for countries in '{s}'")

    # location search
    place_entity = locationtagger.find_locations(text = s)
    if place_entity.countries:
        return (place_entity.countries, 'locationtagger')
    elif place_entity.country_regions:
        if len(place_entity.country_regions) == 1: # no ambiguity
            return (list(place_entity.country_regions.keys()), 'locationtagger')
        elif google_maps_api_key:
            return (_advanced_geo_lookup(s, api_key=google_maps_api_key), 'GoogleMaps')
        else:
            return ([], '')
    else:
        if len(place_entity.country_cities) == 1: # no ambiguity
            return (list(place_entity.country_cities.keys()), 'locationtagger')
        elif google_maps_api_key:
            return (_advanced_geo_lookup(s, api_key=google_maps_api_key), 'GoogleMaps')
        else:
            return ([], '')

def _advanced_geo_lookup(address, api_key=None):
    gmaps = googlemaps.Client(key=api_key)
    place_search = gmaps.find_place(address, "textquery", fields=["formatted_address", "place_id"])
    try:
        place_entity = locationtagger.find_locations(text = place_search['candidates'][0]['formatted_address'])
        return place_entity.countries
    except:
        return []