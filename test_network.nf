nextflow.enable.dsl=2

process TEST_NAT {
    script:
    """
    curl -I https://google.com
    """
}

workflow {
    TEST_NAT()
}