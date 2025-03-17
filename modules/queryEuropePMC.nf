process QUERY_EUROPEPMC {
    label 'process_tiny'
    debug true

    input:
    path(accession_types)
    val page_size
    val limit
    val db
    val db_creds

    output:
    path("epmc_jsons/**.json"), emit: epmc_jsons

    script:
    """
    query_epmc.py --accession-types ${accession_types} --outdir epmc_jsons/ \
    --page-size ${page_size} --limit ${limit} --db ${db} --dbcreds ${db_creds}
    """
}