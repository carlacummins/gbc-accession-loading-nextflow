nextflow.enable.dsl=2

// Run the workflow
include { WRITE_TO_GBC } from './modules/writeToGBC'

workflow {
    main:
        Channel.fromPath(params.input_dir + "/**/*.json")
        | map { json ->
            [json, file(params.accession_types), params.db, file(params.db_creds)]
        } | WRITE_TO_GBC
}
