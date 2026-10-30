# TermsLens mini project report

- `TermsLens_Mini_Project_Report.pdf`: submission-ready report with adapted cover, contents, architecture/recovery diagrams, supplied output images and references.
- `TermsLens_Mini_Project_Report.docx`: editable companion; Word pagination may differ from the PDF.
- Cover reference: page 1 of `Assignment_10_Report.pdf`; title changed to Mini Project in the generated copy only.
- Output evidence: all six pages of `sampe output.pdf` contribute cropped figures. The recorded Privacy scope, Rules-only fallback, 70% coverage and 0–52 score range remain visible and are explained.

The report distinguishes implemented features, recorded checks and pending acceptance. It does not claim live Hybrid success or general-policy accuracy from the supplied print. Security/privacy, API rate limits, URL protection, durable provider quotas, failure recovery and known limits have dedicated coverage.

Regenerate from the project root with the host Python and already-installed `pypdfium2`, `Pillow`, `reportlab`, `python-docx`:

```powershell
python scripts/build_project_report.py
```

The builder makes no database/network/provider calls. It preserves supplied sources and puts intermediate images/source hashes in private `var/report-build`. Generated figures are embedded in both deliverables; they do not depend on external image paths when opened.
