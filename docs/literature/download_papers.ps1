# Download the open-access papers from the Study 2 novelty audit (2026-11-18).
#
#   cd F:\499project\snomedAgent
#   powershell -ExecutionPolicy Bypass -File docs\literature\download_papers.ps1
#
# PDFs go to docs\literature\pdf\ , which is Git-ignored: the papers are other
# authors' copyrighted work, so they stay on this laptop and are never pushed.
# Files that already exist are skipped, so the script can be re-run safely.
# arXiv asks automated clients to pause between requests; the script waits 3 s.
#
# Not downloadable here (behind a paywall; get them through the NSU library):
#   - Ali, Taha, Morsey. J Biomed Inform 175:104993 (2026). doi:10.1016/j.jbi.2026.104993
#   - Liu et al. IEEE ICHI 2024, pp. 248-256. doi:10.1109/ICHI61247.2024.00040
# No PDF published: Kaur, NeurIPS 2026 WiML poster; Schmid et al., GMDS 2025 abstract.

$ErrorActionPreference = "Stop"
$target = Join-Path $PSScriptRoot "pdf"
New-Item -ItemType Directory -Force -Path $target | Out-Null

$papers = [ordered]@{
    "01_Xu2024_GenerateOnGraph_EMNLP.pdf"            = "https://aclanthology.org/2024.emnlp-main.1023.pdf"
    "02_Zhou2026_BRINK_EACL.pdf"                     = "https://aclanthology.org/2026.eacl-long.114.pdf"
    "03_Wang2026_MissDiag_preprint.pdf"              = "https://arxiv.org/pdf/2608.18489"
    "04_Joren2025_SufficientContext_ICLR.pdf"        = "https://arxiv.org/pdf/2411.06037"
    "05_Song2025_TrustScore_ICLR.pdf"                = "https://arxiv.org/pdf/2409.11242"
    "06_Min2026_CROWN-QA_preprint.pdf"               = "https://arxiv.org/pdf/2608.04591"
    "07_Han2026_RAGvsGraphRAG_KDD.pdf"               = "https://arxiv.org/pdf/2502.11371"
    "08_Soman2024_KG-RAG_Bioinformatics_preprint.pdf" = "https://arxiv.org/pdf/2311.17330"
    "09_Hu2026_GuidelineKGQuality_preprint.pdf"      = "https://arxiv.org/pdf/2608.28360"
    "10_Zerhoudi2026_ContextEnrichment_ECMLPKDD.pdf" = "https://arxiv.org/pdf/2606.29645"
    "11_Sun2024_TaxoGlimpse_PVLDB.pdf"               = "https://vldb.org/pvldb/vol17/p2919-sun.pdf"
    "12_He2023_OntoLAMA_ACLFindings.pdf"             = "https://aclanthology.org/2023.findings-acl.213.pdf"
    "13_Dong2024_ConceptPlacement_ESWC.pdf"          = "https://arxiv.org/pdf/2402.17897"
    "14_Dilworth_OOVHierarchicalRetrieval_TGDK.pdf"  = "https://arxiv.org/pdf/2511.16698"
    "15_Do2024_OntologyIDMapping.pdf"                = "https://arxiv.org/pdf/2409.13746"
    "16_Sharma2025_OG-RAG_EMNLP.pdf"                 = "https://aclanthology.org/2025.emnlp-main.1674.pdf"
    "17_Chang2024_SNOMEDinLLMs_JMIRMedInform.pdf"    = "https://medinform.jmir.org/2024/1/e62924/PDF"
    "18_UcedaSosa_HierarchyConsistency_preprint.pdf" = "https://arxiv.org/pdf/2405.20163"
    "19_Razniewski2024_Completeness_ACMCSUR.pdf"     = "https://arxiv.org/pdf/2305.05403"
    "20_Vaidya_OntologyBenchmarks_preprint.pdf"      = "https://arxiv.org/pdf/2610.00682"
    "21_Born2026_SNOMEDcodeHallucination_ClinicalNLP.pdf" = "https://aclanthology.org/2026.clinicalnlp-1.41.pdf"
}

$ok = 0; $skipped = 0; $failed = @()
foreach ($name in $papers.Keys) {
    $out = Join-Path $target $name
    if (Test-Path $out) { $skipped++; continue }
    try {
        Invoke-WebRequest -Uri $papers[$name] -OutFile $out -UserAgent "snomedAgent-literature/1.0" -UseBasicParsing
        $head = [System.IO.File]::ReadAllBytes($out)[0..3]
        if ([System.Text.Encoding]::ASCII.GetString($head) -ne "%PDF") {
            Remove-Item $out
            throw "not a PDF (the site returned a web page)"
        }
        Write-Host "saved   $name"
        $ok++
    } catch {
        Write-Host "FAILED  $name : $($_.Exception.Message)"
        $failed += $name
    }
    if ($papers[$name] -like "*arxiv.org*") { Start-Sleep -Seconds 3 }
}
Write-Host ""
Write-Host "$ok downloaded, $skipped already present, $($failed.Count) failed. Folder: $target"
if ($failed.Count) { Write-Host "Open the failed ones in a browser instead: see the URLs in this script." }
