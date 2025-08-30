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
pip install streamlit python-dotenv google-generativeai
```

## Environment variables

1) Copy the example file and set your real values:

```bash
cp .env.example .env
```

2) Edit `.env` and provide values for the listed variables.

## Run

```bash
streamlit run app.py
```

## Troubleshooting

- If you see `Brak klucza API`, make sure `GEMINI_API_KEY` is set in `.env`.
- If imports fail, confirm the dependencies are installed in your active environment.
