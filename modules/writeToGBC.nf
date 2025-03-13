process WRITE_TO_GBC {
    label 'process_tiny'
    debug true

    secret 'SQLUSER'
    secret 'SQLPASS'

    input:
    tuple path(json_file), path(accession_types), val(db) //, val(db_user), val(db_pass)

    output:
    path(summary_file)

    script:
    summary_file = "${json_file.baseName}.summary.txt"
    """
    load_to_gbc.py --json ${json_file} --accession-types ${accession_types} --summary ${summary_file} \
    --db ${db} --sqluser \$SQLUSER --sqlpass \$SQLPASS
    """
}