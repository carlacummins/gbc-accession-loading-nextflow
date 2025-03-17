process QUERY_EUROPEPMC {
    label 'process_tiny'
    debug true

    secret 'CLOUD_SQL_USER'
    secret 'CLOUD_SQL_PASSWORD'

    input:
    path(accession_types)
    val page_size
    val limit
    val db
    // val db_user
    // val db_pass

    output:
    path("epmc_jsons/**.json"), emit: epmc_jsons
    // path("cursors.txt"), emit: cursors

    script:
    """
    query_epmc.py --accession-types ${accession_types} --outdir epmc_jsons/ \
    --page-size ${page_size} --limit ${limit} --db ${db} \
    --sqluser \$CLOUD_SQL_USER --sqlpass \$CLOUD_SQL_PASSWORD
    """
}