# LexOrchestra

LexOrchestra is a Streamlit-based workspace for organizing client documents and running contract analysis workflows. It stores raw documents, anonymized copies, and generated risk reports in a structured local vault and provides UI modules for analysis, templates, and automated drafting.

Disclaimer: All client names, documents, and data in this repository are fictional demo content created for testing and examples only. No real client data or confidential information is included.

The app focuses on processing legal texts with a lightweight UI, using a single entry point and a small set of external dependencies. It is intended for personal use and local execution.

## Sample data

Static demo inputs live in `examples/lex_repo_demo/` and include raw contract samples only. Generated outputs (anonymized copies, analysis reports, emails, and auto-generated drafts) are created at runtime under `lex_repo/` and are intentionally excluded from version control.

## Prerequisites

- Python 3.9+ and `pip`

## Installation

1) Create and activate a virtual environment.
2) Install dependencies:

```bash
pip install -r requirements.txt
```

## Environment variables

Set `GEMINI_API_KEY` in the process environment or in the shared secret store
`~/.config/api secrets/global.env`. Do not commit real credentials.

## Run

```bash
streamlit run app.py
```

## Troubleshooting

- If you see `Brak klucza API`, make sure `GEMINI_API_KEY` is set in the process environment or shared secret store.
- If imports fail, confirm the dependencies are installed in your active environment.

## Prototype scope and offline checks

This is a local demo, with no authentication or access controls. Generated drafts,
risk reports, and regex anonymization require human review; anonymization is not a
privacy guarantee. Do not submit confidential or real client data. Gemini calls
send text to an external service and may incur costs. The legacy Gemini SDK is
retained for this prototype; live API compatibility is not verified by offline CI.

The vault rejects path traversal and preserves repeated saves with unique filenames.
It assumes trusted local filesystem access, not a hostile multi-user environment.

```sh
python -m unittest discover -s tests -v
python -m compileall -q app.py vault.py
```

Tests use temporary directories and require neither an API key nor external packages.
