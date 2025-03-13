nextflow.enable.dsl=2

// Run the workflow
include { QUERY_EUROPEPMC } from './modules/queryEuropePMC'
include { WRITE_TO_GBC } from './modules/writeToGBC'

workflow {
    main:
        limit = params.limit ?: 0

        query = QUERY_EUROPEPMC(
            file(params.accession_types),
            params.page_size,
            limit,
            params.db,
            params.db_user,
            params.db_pass
        )
        query.epmc_jsons | flatten
        | view

        query.epmc_jsons
        | flatten
        | map { json ->
            [json, file(params.accession_types), params.db] //, params.db_user, params.db_pass]
        } | WRITE_TO_GBC
}
