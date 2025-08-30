import os
import re
import time
import json
from datetime import datetime
from typing import Dict, Tuple, List
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv
import google.generativeai as genai

st.set_page_config(page_title="LexOrchestra Vault", layout="wide")

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    st.error("Brak klucza API! Ustaw GEMINI_API_KEY w pliku .env")
    st.stop()

genai.configure(api_key=api_key)

REPO_ROOT = "lex_repo"
TEMPLATES_ROOT = "lex_templates"

DOC_TYPE_LABELS = {
    "raw": "Umowy – oryginały",
    "anonymized": "Umowy – zanonimizowane",
    "analysis": "Raporty ryzyk",
    "email": "Korespondencja",
}


class VaultManager:
    DOC_TYPES = ("raw", "anonymized", "analysis", "email")

    def __init__(self, root_dir: str = REPO_ROOT):
        self.root = Path(root_dir)
        self.root.mkdir(exist_ok=True)

    def get_clients(self) -> List[str]:
        return [d.name for d in self.root.iterdir() if d.is_dir()]

    def ensure_client_folder(self, client_name: str) -> Path:
        client_path = self.root / client_name
        client_path.mkdir(exist_ok=True)
        for sub in self.DOC_TYPES:
            (client_path / sub).mkdir(exist_ok=True)
        return client_path

    def save_document(
        self,
        client_name: str,
        doc_type: str,
        content: str,
        extension: str = "txt",
        label: str = "",
    ) -> str:
        client_path = self.ensure_client_folder(client_name)

        if doc_type not in self.DOC_TYPES:
            subdir = client_path / "other"
            subdir.mkdir(exist_ok=True)
        else:
            subdir = client_path / doc_type

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if label:
            slug = label.lower().replace(" ", "-")
            slug = re.sub(r"[^a-z0-9_-]", "", slug)
            slug = slug.strip("-")
            if slug:
                filename = f"{timestamp}_{doc_type}_{slug}.{extension}"
            else:
                filename = f"{timestamp}_{doc_type}.{extension}"
        else:
            filename = f"{timestamp}_{doc_type}.{extension}"

        file_path = subdir / filename

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return str(file_path)

    def get_client_files(self, client_name: str) -> Dict[str, List[str]]:
        client_path = self.root / client_name
        if not client_path.exists():
            return {}

        result: Dict[str, List[str]] = {}
        for sub in client_path.iterdir():
            if sub.is_dir():
                files = sorted([f.name for f in sub.iterdir() if f.is_file()])
                if files:
                    result[sub.name] = files
        return result

    def read_document(self, client_name: str, doc_type: str, filename: str) -> str:
        client_path = self.root / client_name
        if doc_type in self.DOC_TYPES:
            subdir = client_path / doc_type
        else:
            subdir = client_path / "other"
        path = subdir / filename
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
        return ""

    def list_recent_documents(self, client_name: str, limit: int = 10) -> List[Dict[str, str]]:
        client_path = self.root / client_name
        if not client_path.exists():
            return []

        entries = []
        for doc_type in self.DOC_TYPES:
            subdir = client_path / doc_type
            if subdir.exists() and subdir.is_dir():
                for file in subdir.iterdir():
                    if file.is_file():
                        name = file.name
                        parts = name.split("_", 1)
                        timestamp_str = parts[0] if parts else ""
                        dt = None
                        try:
                            dt = datetime.strptime(timestamp_str, "%Y%m%d")
                        except ValueError:
                            pass
                        entries.append({
                            "doc_type": doc_type,
                            "filename": name,
                            "path": str(file),
                            "timestamp": timestamp_str,
                            "dt": dt,
                        })

        entries.sort(key=lambda x: (x["dt"] is None, x["dt"]), reverse=True)
        entries.sort(key=lambda x: x["dt"] is None)
        entries_with_dt = [e for e in entries if e["dt"] is not None]
        entries_without_dt = [e for e in entries if e["dt"] is None]
        entries_with_dt.sort(key=lambda x: x["dt"], reverse=True)
        sorted_entries = entries_with_dt + entries_without_dt

        result = []
        for e in sorted_entries[:limit]:
            result.append({
                "doc_type": e["doc_type"],
                "filename": e["filename"],
                "path": e["path"],
                "timestamp": e["timestamp"],
            })
        return result


def ask_llm(system_prompt: str, user_prompt: str) -> str:
    try:
        model = genai.GenerativeModel("models/gemini-2.5-flash")
        full_prompt = f"{system_prompt}\n\nUŻYTKOWNIK:\n{user_prompt}"
        response = model.generate_content(full_prompt)
        return response.text
    except Exception as e:
        return f"Błąd API: {str(e)}"


def anonymize(text: str) -> Tuple[str, Dict[str, str]]:
    mapping: Dict[str, str] = {}
    counter = {"OSOBA": 0, "SPOLKA": 0, "PESEL": 0, "NIP": 0, "KONTO": 0}

    def store(prefix: str, value: str) -> str:
        counter[prefix] += 1
        token = f"[{prefix}_{counter[prefix]}]"
        mapping[token] = value
        return token

    text = re.sub(r"\b\d{26}\b", lambda m: store("KONTO", m.group()), text)
    text = re.sub(r"\b\d{11}\b", lambda m: store("PESEL", m.group()), text)
    text = re.sub(r"\b\d{10}\b", lambda m: store("NIP", m.group()), text)
    text = re.sub(
        r"\b([A-Z][A-Za-z0-9&\- ]+ Sp\. z o\.o\.)\b",
        lambda m: store("SPOLKA", m.group(1)),
        text,
    )
    text = re.sub(
        r"\b([A-ZŻŹĆĄŚĘŁÓŃ][a-zżźćńółęąś]+ [A-ZŻŹĆĄŚĘŁÓŃ][a-zżźćńółęąś]+)\b",
        lambda m: store("OSOBA", m.group(1)),
        text,
    )
    return text, mapping


class ContractReviewerAgent:
    def run(self, contract_text: str) -> str:
        system = (
            "Jesteś doświadczonym prawnikiem. "
            "Na podstawie treści umowy wypisz maksymalnie 5 kluczowych ryzyk "
            "oraz rekomendacje zmian. Pisz krótko, w punktach."
        )
        user = f"UMOWA:\n{contract_text}"
        return ask_llm(system, user)


class RiskMatrixAgent:

    def run(self, contract_text: str) -> List[Dict[str, str]]:
        system_prompt = (
            "Jesteś doświadczonym prawnikiem. Przeanalizuj podaną umowę i zidentyfikuj "
            "najważniejsze ryzyka (maksymalnie 10).\n\n"
            "WAŻNE INSTRUKCJE:\n"
            "- Odpowiedz WYŁĄCZNIE poprawnym JSON-em.\n"
            "- NIE dodawaj żadnych wyjaśnień, komentarzy ani tekstu.\n"
            "- NIE używaj znaczników markdown ani bloków kodu (```json).\n"
            "- Zwróć ALBO obiekt z kluczem 'risks' zawierającym listę, ALBO bezpośrednio listę.\n\n"
            "Każdy obiekt ryzyka MUSI zawierać pola:\n"
            "- \"title\": krótki tytuł ryzyka\n"
            "- \"area\": obszar (np. \"odpowiedzialność\", \"wypowiedzenie\", \"płatności\", "
            "\"własność intelektualna\", \"dane osobowe\")\n"
            "- \"severity\": \"LOW\", \"MEDIUM\" lub \"HIGH\"\n"
            "- \"description\": krótki opis ryzyka\n"
            "- \"mitigation\": krótka rekomendacja, jak ograniczyć to ryzyko\n\n"
            "Przykład poprawnej odpowiedzi:\n"
            '[{"title": "Brak limitu odpowiedzialności", "area": "odpowiedzialność", '
            '"severity": "HIGH", "description": "Umowa nie określa maksymalnej kwoty odszkodowania", '
            '"mitigation": "Dodać klauzulę ograniczającą odpowiedzialność do wartości kontraktu"}]'
        )
        user_prompt = (
            "To jest treść umowy. Na jej podstawie zbuduj strukturę JSON z ryzykami:\n\n"
            + contract_text
        )
        response_text = ask_llm(system_prompt, user_prompt)

        raw = response_text.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "risks" in parsed and isinstance(parsed["risks"], list):
                return parsed["risks"]
            elif isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError, KeyError, ValueError):
            pass
        return []


class EmailBriefAgent:

    def run(self, contract_text: str, risk_list: list) -> str:
        system_prompt = (
            "Jesteś prawnikiem piszącym e-mail do klienta biznesowego.\n"
            "Twoim zadaniem jest przygotowanie jasnego, profesjonalnego e-maila w języku polskim, "
            "który podsumowuje wyniki analizy umowy.\n\n"
            "E-mail powinien:\n"
            "- zaczynać się od krótkiego powitania i jednego zdania wyjaśniającego, czego dotyczy wiadomość,\n"
            "- krótko podsumować, jakiego typu jest to umowa,\n"
            "- wymienić najważniejsze zidentyfikowane ryzyka w formie listy punktowej,\n"
            "- dla każdego ryzyka zawierać krótkie wyjaśnienie i rekomendację,\n"
            "- kończyć się krótką sugestią dalszych kroków i grzecznym pożegnaniem.\n\n"
            "Ton powinien być profesjonalny, ale zrozumiały dla osoby niebędącej prawnikiem.\n"
            "Wynik musi być zwykłym tekstem e-maila, bez JSON-a ani formatowania markdown."
        )
        user_prompt = (
            "Poniżej masz treść umowy (po anonimizacji) oraz listę ryzyk w formacie listy słowników. "
            "Na tej podstawie napisz treść maila do klienta.\n\n"
            "TREŚĆ UMOWY:\n" + contract_text + "\n\n"
            "LISTA RYZYK:\n" + str(risk_list)
        )
        try:
            return ask_llm(system_prompt, user_prompt)
        except Exception:
            return "Nie udało się wygenerować podsumowania e-mail."


class DraftContractAgent:

    def run(self, description: str) -> str:
        system_prompt = (
            "Jesteś doświadczonym prawnikiem.\n"
            "Na podstawie krótkiego opisu od użytkownika musisz przygotować pełny projekt umowy "
            "(np. umowa B2B, NDA, umowa o dzieło itp.), który pasuje do opisu.\n"
            "Wynik powinien być kompletnym tekstem umowy w języku polskim, gotowym do dalszej weryfikacji.\n"
            "Zwróć wyłącznie tekst umowy, bez wyjaśnień, bez markdown."
        )
        user_prompt = (
            "Na podstawie poniższego opisu przygotuj pełną treść umowy:\n\n" + description
        )
        try:
            return ask_llm(system_prompt, user_prompt)
        except Exception:
            return "Nie udało się wygenerować projektu umowy."


class ContractRefinerAgent:

    def run(self, contract_text: str, risk_list: list) -> str:
        system_prompt = (
            "Jesteś doświadczonym prawnikiem.\n"
            "Twoim zadaniem jest przepisanie podanej umowy tak, aby ograniczyć zidentyfikowane ryzyka, "
            "zachowując jednocześnie cel biznesowy umowy.\n"
            "Dla każdego ryzyka z listy wprowadź odpowiednie zmiany w treści umowy.\n"
            "Zwróć pełny, poprawiony tekst umowy w języku polskim, bez wyjaśnień, bez markdown."
        )
        user_prompt = (
            "ORYGINALNA UMOWA:\n" + contract_text + "\n\n"
            "LISTA RYZYK DO OGRANICZENIA:\n" + str(risk_list) + "\n\n"
            "Na podstawie powyższych informacji przygotuj poprawioną wersję umowy."
        )
        try:
            return ask_llm(system_prompt, user_prompt)
        except Exception:
            return "Nie udało się wygenerować poprawionej wersji umowy."


class ContractFollowUpAgent:

    def run(self, contract_text: str, risk_list: list, followup_prompt: str) -> str:
        system_prompt = (
            "Jesteś doświadczonym prawnikiem.\n"
            "Otrzymujesz:\n"
            "- pełną treść umowy,\n"
            "- listę zidentyfikowanych ryzyk (z tytułami, poziomami, opisami itp.),\n"
            "- dodatkową instrukcję lub pytanie od użytkownika.\n\n"
            "Twoim zadaniem jest odpowiedzieć na tę instrukcję lub pytanie w sposób przydatny dla prawnika:\n"
            "- możesz zaproponować zmiany w konkretnych klauzulach,\n"
            "- zaproponować nowe sformułowania,\n"
            "- wyjaśnić ryzyka lub ich konsekwencje,\n"
            "- zasugerować strategię negocjacyjną,\n"
            "w zależności od tego, o co pyta użytkownik.\n\n"
            "Wynik powinien być zwykłym tekstem w języku polskim, bez JSON-a ani formatowania markdown."
        )
        user_prompt = (
            "Poniżej znajduje się treść umowy, lista zidentyfikowanych ryzyk oraz dodatkowe pytanie/instrukcja od użytkownika.\n\n"
            "TREŚĆ UMOWY:\n" + contract_text + "\n\n"
            "LISTA RYZYK:\n" + str(risk_list) + "\n\n"
            "DODATKOWE PYTANIE/INSTRUKCJA:\n" + followup_prompt
        )
        try:
            return ask_llm(system_prompt, user_prompt)
        except Exception:
            return "Nie udało się wygenerować odpowiedzi na dodatkowy prompt."


class RouterAgent:
    def decide(self, query: str) -> str:
        system_prompt = (
            "Jesteś asystentem prawnym. Na podstawie opisu zadania użytkownika "
            "zdecyduj, który moduł jest odpowiedni.\n\n"
            "Odpowiedz TYLKO jednym słowem:\n"
            "- ANALYSIS – jeśli użytkownik chce przeanalizować istniejącą umowę, "
            "sprawdzić ryzyka, zanonimizować treść umowy lub przejrzeć klauzule.\n"
            "- TEMPLATES – jeśli użytkownik chce wybrać wzór umowy, przygotować nową umowę "
            "na podstawie szablonu lub dobrać typ umowy.\n"
            "- UNKNOWN – jeśli nie jesteś pewien lub zapytanie nie pasuje do żadnego modułu.\n\n"
            "Odpowiedz TYLKO: ANALYSIS, TEMPLATES lub UNKNOWN."
        )
        response = ask_llm(system_prompt, query)
        normalized = response.strip().upper()
        if "ANALYSIS" in normalized:
            return "ANALYSIS"
        elif "TEMPLATES" in normalized:
            return "TEMPLATES"
        return "UNKNOWN"


class OrchestratorPlannerAgent:

    def run(self, description: str, template_names: list) -> dict:
        system_prompt = (
            "Jesteś asystentem AI pomagającym prawnikowi wybrać sposób rozpoczęcia pracy "
            "na podstawie krótkiego opisu zadania.\n\n"
            "Otrzymujesz:\n"
            "- listę dostępnych nazw szablonów umów,\n"
            "- opis w języku naturalnym tego, co użytkownik chce zrobić.\n\n"
            "Twoje zadanie to zdecydować:\n"
            "- czy użyć szablonu do stworzenia nowej umowy dla klienta,\n"
            "- jeśli tak, który szablon z podanej listy najlepiej pasuje,\n"
            "- który moduł powinien być użyty następnie (zwykle ANALYSIS, jeśli umowa zostanie utworzona).\n\n"
            "Odpowiedz WYŁĄCZNIE obiektem JSON, bez dodatkowego tekstu, bez markdown i bez wyjaśnień.\n"
            "JSON musi mieć klucze:\n"
            "- \"use_template\": true lub false,\n"
            "- \"chosen_template\": dokładna nazwa szablonu z podanej listy lub pusty string jeśli żaden nie pasuje,\n"
            "- \"next_tool\": jeden z: \"ANALYSIS\", \"TEMPLATES\" lub \"NONE\",\n"
            "- \"summary\": krótkie wyjaśnienie po polsku dla użytkownika (1-3 zdania)."
        )
        templates_list = "\n".join(template_names) if template_names else "(brak szablonów)"
        user_prompt = (
            f"DOSTĘPNE SZABLONY:\n{templates_list}\n\n"
            f"OPIS ZADANIA:\n{description}\n\n"
            "Na podstawie powyższych wzorów i opisu zdecyduj, czy użyć któregoś wzoru "
            "i który moduł wybrać. Zwróć wyłącznie JSON w wymaganym formacie."
        )
        response_text = ask_llm(system_prompt, user_prompt)

        raw = response_text.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        try:
            parsed = json.loads(raw)
            if (
                isinstance(parsed, dict)
                and "use_template" in parsed
                and "chosen_template" in parsed
                and "next_tool" in parsed
                and "summary" in parsed
            ):
                return parsed
        except (json.JSONDecodeError, TypeError, KeyError, ValueError):
            pass

        return {
            "use_template": False,
            "chosen_template": "",
            "next_tool": "NONE",
            "summary": "Nie udało się zaplanować automatycznego działania.",
        }


def seed_templates_if_empty() -> None:
    root = Path(TEMPLATES_ROOT)
    root.mkdir(exist_ok=True)

    if any(root.iterdir()):
        return

    templates = {
        "umowa_b2b_standard.txt": "SZKIC: Umowa B2B (standardowa) – tekst przykładowy, tylko do celów demonstracyjnych.",
        "umowa_nda_polsko_angielska.txt": "SZKIC: Umowa o zachowaniu poufności (NDA) – tekst przykładowy, tylko do celów demonstracyjnych.",
        "umowa_o_dzielo_freelancer.txt": "SZKIC: Umowa o dzieło dla freelancera – tekst przykładowy, tylko do celów demonstracyjnych.",
    }

    for filename, content in templates.items():
        (root / filename).write_text(content, encoding="utf-8")


def get_available_templates() -> List[Path]:
    root = Path(TEMPLATES_ROOT)
    root.mkdir(exist_ok=True)
    return sorted(root.glob("*.txt"), key=lambda p: p.name)


def seed_demo_clients(vault: VaultManager):
    demo_clients = [
        "Nowak i Wspólnicy Sp. z o.o.",
        "ABC Logistics Sp. z o.o.",
        "Klient indywidualny – Jan Kowalski",
    ]

    for client_name in demo_clients:
        vault.ensure_client_folder(client_name)

        vault.save_document(
            client_name,
            "raw",
            "To jest przykładowa umowa B2B dla klienta demo, tylko do celów prezentacji.",
            extension="txt",
            label="demo_umowa_b2b",
        )

        vault.save_document(
            client_name,
            "analysis",
            "To jest przykładowy raport ryzyk do celów demonstracyjnych.",
            extension="md",
            label="demo_raport_ryzyk",
        )


def render_sidebar(vault: VaultManager):
    st.sidebar.header("Repozytorium (Vault)")

    mode = st.sidebar.radio("Tryb:", ["Wybierz Klienta", "Nowy Klient"])

    selected_client = None

    if mode == "Nowy Klient":
        new_client = st.sidebar.text_input("Nazwa klienta")
        if st.sidebar.button("Utwórz folder"):
            if new_client:
                vault.ensure_client_folder(new_client)
                st.sidebar.success(f"Utworzono klienta: {new_client}")
                st.session_state["tool"] = "HOME"
                st.rerun()
    else:
        clients = vault.get_clients()
        if clients:
            previous_client = st.session_state.get("selected_client", None)
            selected_client = st.sidebar.selectbox("Aktywny klient:", clients)
            if selected_client != previous_client:
                st.session_state["selected_client"] = selected_client
                st.session_state["tool"] = "HOME"
                st.rerun()
            st.sidebar.markdown("---")
            st.sidebar.caption(f"Pliki w lex_repo/{selected_client}:")

            files_by_type = vault.get_client_files(selected_client)
            if not files_by_type:
                st.sidebar.text("Brak plików.")
            else:
                for doc_type, files in files_by_type.items():
                    label = DOC_TYPE_LABELS.get(doc_type, doc_type.upper())
                    st.sidebar.markdown(f"**{label}**")
                    for f in files:
                        st.sidebar.text(f"{f}")
        else:
            st.sidebar.warning("Brak klientów w bazie.")

    return selected_client


def render_analysis_module(client_name: str, vault: VaultManager):
    st.markdown(f"### Moduł: Analiza i Anonimizacja | Klient: **{client_name}**")
    st.info(
        "Ten moduł automatycznie zapisuje oryginał, wersję bezpieczną i raport "
        "w strukturze Vault (`raw/`, `anonymized/`, `analysis/`)."
    )

    if "analysis_contract_text" not in st.session_state:
        st.session_state["analysis_contract_text"] = ""
    if "analysis_anon_text" not in st.session_state:
        st.session_state["analysis_anon_text"] = ""
    if "analysis_mapping" not in st.session_state:
        st.session_state["analysis_mapping"] = {}
    if "analysis_review" not in st.session_state:
        st.session_state["analysis_review"] = ""
    if "analysis_risk_list" not in st.session_state:
        st.session_state["analysis_risk_list"] = []
    if "analysis_email_body" not in st.session_state:
        st.session_state["analysis_email_body"] = ""

    files_by_type = vault.get_client_files(client_name)
    raw_files = files_by_type.get("raw", [])

    if raw_files:
        selected_filename = st.selectbox("Lub wybierz istniejącą umowę klienta:", raw_files)
        if st.button("Załaduj wybraną umowę do pola poniżej"):
            content = vault.read_document(client_name, "raw", selected_filename)
            if content:
                st.session_state["analysis_contract_text"] = content
                st.rerun()

    st.text_area(
        "Wklej treść umowy B2B:",
        height=250,
        key="analysis_contract_text",
    )

    if st.button("Uruchom procedurę analizy"):
        contract_text = st.session_state.get("analysis_contract_text", "")
        if not contract_text.strip():
            st.warning("Pusto – wklej treść umowy.")
        else:
            status = st.empty()

            status.info("1/4 Zapisuję oryginał w Vault (raw)...")
            vault.save_document(client_name, "raw", contract_text, "txt", label="umowa_b2b")
            time.sleep(0.4)

            status.info("2/4 LexGuardian: Anonimizacja danych wrażliwych...")
            anon_text, mapping = anonymize(contract_text)
            vault.save_document(client_name, "anonymized", anon_text, "txt", label="umowa_b2b_anonimizowana")
            time.sleep(0.4)

            status.info("3/4 ContractReviewer: Analiza ryzyk umowy...")
            review = ContractReviewerAgent().run(contract_text)
            vault.save_document(client_name, "analysis", review, "md", label="raport_ryzyk")
            time.sleep(0.4)

            status.info("4/5 RiskMatrixAgent: Budowanie tabeli ryzyk...")
            risk_agent = RiskMatrixAgent()
            risk_list = risk_agent.run(contract_text)
            vault.save_document(
                client_name,
                "analysis",
                json.dumps({"risks": risk_list}, ensure_ascii=False, indent=2),
                "json",
                label="raport_ryzyk_struktura",
            )
            time.sleep(0.4)

            status.success("Proces zakończony! Pliki zapisane w Vault klienta.")

            st.session_state["analysis_anon_text"] = anon_text
            st.session_state["analysis_mapping"] = mapping
            st.session_state["analysis_review"] = review
            st.session_state["analysis_risk_list"] = risk_list
            st.session_state["analysis_email_body"] = ""

    anon_text = st.session_state.get("analysis_anon_text", "")
    mapping = st.session_state.get("analysis_mapping", {})
    review = st.session_state.get("analysis_review", "")
    risk_list = st.session_state.get("analysis_risk_list", [])

    if anon_text:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Anonimizacja")
            st.text_area("Bezpieczny tekst", anon_text, height=300, key="analysis_anon_display")
            with st.expander("Mapa danych wrażliwych (token → oryginał)"):
                st.json(mapping)
        with c2:
            st.subheader("Raport ryzyk")
            st.markdown(review)

        st.subheader("Tabela ryzyk")
        if not risk_list:
            st.info("Brak zidentyfikowanych ryzyk w formacie tabelarycznym.")
        else:
            severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
            sorted_risks = sorted(
                risk_list,
                key=lambda r: severity_order.get(r.get("severity", "").upper(), 3)
            )
            rows = []
            for item in sorted_risks:
                rows.append({
                    "Tytuł": item.get("title", ""),
                    "Obszar": item.get("area", ""),
                    "Poziom": item.get("severity", ""),
                    "Opis": item.get("description", ""),
                    "Rekomendacja": item.get("mitigation", ""),
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)

        st.subheader("Podsumowanie e-mail dla klienta")
        if st.button("Wygeneruj podsumowanie e-mail dla klienta"):
            contract_text = st.session_state.get("analysis_contract_text", "")
            email_agent = EmailBriefAgent()
            email_body_result = email_agent.run(contract_text, risk_list)
            vault.save_document(
                client_name,
                "email",
                email_body_result,
                "md",
                label="podsumowanie_mailowe",
            )
            st.session_state["analysis_email_body"] = email_body_result

        if st.session_state.get("analysis_email_body", ""):
            st.text_area(
                "Treść maila do skopiowania",
                value=st.session_state["analysis_email_body"],
                height=300,
                disabled=True,
                key="email_brief_output",
            )


def render_templates_module(client_name: str, vault: VaultManager) -> None:
    st.markdown(f"### Moduł: Dobór wzoru umowy | Klient: **{client_name}**")

    if "templates_last_copied_text" not in st.session_state:
        st.session_state["templates_last_copied_text"] = None

    templates = get_available_templates()

    if not templates:
        st.warning("Brak dostępnych wzorów umów w katalogu templates.")
        return

    template_names = [p.stem.replace("_", " ").capitalize() for p in templates]
    selected_name = st.selectbox("Wybierz wzór umowy:", template_names)

    selected_index = template_names.index(selected_name)
    selected_path = templates[selected_index]
    template_text = selected_path.read_text(encoding="utf-8")

    st.text_area("Podgląd wybranego wzoru", value=template_text, height=280, disabled=True)

    if st.button("Utwórz kopię tego wzoru jako umowę klienta"):
        vault.save_document(
            client_name,
            "raw",
            template_text,
            extension="txt",
            label=selected_path.stem,
        )
        st.success("Utworzono kopię wzoru w folderze klienta (sekcja: Umowy – oryginały).")
        st.session_state["templates_last_copied_text"] = template_text

    if st.session_state["templates_last_copied_text"]:
        if st.button("Przejdź do modułu: Analiza i Anonimizacja dla tej umowy"):
            st.session_state["analysis_contract_text"] = st.session_state["templates_last_copied_text"]
            st.session_state["templates_last_copied_text"] = None
            st.session_state["tool"] = "ANALYSIS"
            st.rerun()


def render_orchestrator_module(client_name: str, vault: VaultManager):
    if "orchestrator_last_decision" not in st.session_state:
        st.session_state["orchestrator_last_decision"] = None
    if "orchestrator_contract_text" not in st.session_state:
        st.session_state["orchestrator_contract_text"] = ""
    if "orchestrator_review" not in st.session_state:
        st.session_state["orchestrator_review"] = ""
    if "orchestrator_risk_list" not in st.session_state:
        st.session_state["orchestrator_risk_list"] = []
    if "orchestrator_refined_contract_text" not in st.session_state:
        st.session_state["orchestrator_refined_contract_text"] = ""
    if "orchestrator_email_body" not in st.session_state:
        st.session_state["orchestrator_email_body"] = ""
    if "orchestrator_plan_message" not in st.session_state:
        st.session_state["orchestrator_plan_message"] = ""
    if "orchestrator_followup_prompt" not in st.session_state:
        st.session_state["orchestrator_followup_prompt"] = ""
    if "orchestrator_followup_response" not in st.session_state:
        st.session_state["orchestrator_followup_response"] = ""

    st.markdown(f"### Asystent główny (Orchestrator) | Klient: **{client_name}**")
    st.info(
        "Opisz zadanie w języku naturalnym, a asystent zaproponuje odpowiedni moduł."
    )

    description = st.text_area("Opisz, co chcesz teraz zrobić", height=150, key="orchestrator_description")

    col_left, col_right = st.columns(2)

    with col_left:
        if st.button("Zaproponuj moduł", use_container_width=True):
            if not description.strip():
                st.warning("Podaj opis zadania, aby uzyskać propozycję modułu.")
            else:
                router = RouterAgent()
                decision = router.decide(description)
                st.session_state["orchestrator_last_decision"] = decision

                if decision == "ANALYSIS":
                    st.success("Rekomendowany moduł: Analiza i Anonimizacja")
                elif decision == "TEMPLATES":
                    st.success("Rekomendowany moduł: Dobór wzoru umowy (Templates)")
                else:
                    st.info("Asystent nie jest pewien, który moduł pasuje do tego zapytania.")

    with col_right:
        if st.button("Tryb automatyczny: wygeneruj projekt umowy", use_container_width=True):
            if not description.strip():
                st.warning("Podaj opis zadania, aby uruchomić tryb automatyczny.")
            elif not client_name:
                st.error("Brak aktywnego klienta. Wybierz klienta w panelu bocznym.")
            else:
                contract_text = DraftContractAgent().run(description)
                vault.save_document(
                    client_name,
                    "raw",
                    contract_text,
                    "txt",
                    label="orchestrator_projekt_umowy",
                )

                st.session_state["orchestrator_contract_text"] = contract_text
                st.session_state["orchestrator_review"] = ""
                st.session_state["orchestrator_risk_list"] = []
                st.session_state["orchestrator_refined_contract_text"] = ""
                st.session_state["orchestrator_email_body"] = ""
                st.session_state["orchestrator_followup_response"] = ""
                st.session_state["orchestrator_plan_message"] = "Wygenerowano projekt umowy w trybie automatycznym."

    last_decision = st.session_state["orchestrator_last_decision"]

    if last_decision == "ANALYSIS":
        if st.button("Przejdź do modułu: Analiza i Anonimizacja"):
            st.session_state["tool"] = "ANALYSIS"
            st.rerun()
    elif last_decision == "TEMPLATES":
        if st.button("Przejdź do modułu: Dobór wzoru umowy (Templates)"):
            st.session_state["tool"] = "TEMPLATES"
            st.rerun()

    st.markdown("---")

    plan_message = st.session_state.get("orchestrator_plan_message", "")
    contract_text = st.session_state.get("orchestrator_contract_text", "")

    if plan_message:
        st.info(plan_message)

    if contract_text:
        st.subheader("Projekt umowy (tryb automatyczny)")
        edited_contract = st.text_area(
            "Treść projektu umowy (możesz edytować)",
            value=contract_text,
            height=300,
            key="orchestrator_contract_editor",
        )
        st.session_state["orchestrator_contract_text"] = edited_contract

        st.text_area(
            "Dodatkowy prompt do pracy z tą umową",
            height=100,
            key="orchestrator_followup_prompt_input",
            value=st.session_state.get("orchestrator_followup_prompt", ""),
        )
        st.session_state["orchestrator_followup_prompt"] = st.session_state.get("orchestrator_followup_prompt_input", "")

        if st.button("Wyślij dodatkowy prompt"):
            current_contract = st.session_state.get("orchestrator_contract_text", "")
            current_risks = st.session_state.get("orchestrator_risk_list", [])
            followup_prompt = st.session_state.get("orchestrator_followup_prompt", "")
            if not followup_prompt.strip():
                st.warning("Podaj treść dodatkowego promptu.")
            else:
                followup_result = ContractFollowUpAgent().run(current_contract, current_risks, followup_prompt)
                st.session_state["orchestrator_followup_response"] = followup_result

        if st.session_state.get("orchestrator_followup_response", ""):
            st.text_area(
                "Odpowiedź asystenta na dodatkowy prompt",
                value=st.session_state["orchestrator_followup_response"],
                height=250,
                disabled=True,
                key="orchestrator_followup_response_display",
            )

        st.subheader("Analiza ryzyk (tryb automatyczny)")

        if st.button("Przeanalizuj tę umowę pod kątem ryzyk"):
            current_contract = st.session_state.get("orchestrator_contract_text", "")
            if not current_contract.strip():
                st.warning("Brak treści umowy do analizy.")
            else:
                analysis_review = ContractReviewerAgent().run(current_contract)
                vault.save_document(
                    client_name,
                    "analysis",
                    analysis_review,
                    "md",
                    label="orchestrator_raport_ryzyk",
                )

                analysis_risk_list = RiskMatrixAgent().run(current_contract)
                vault.save_document(
                    client_name,
                    "analysis",
                    json.dumps({"risks": analysis_risk_list}, ensure_ascii=False, indent=2),
                    "json",
                    label="orchestrator_raport_ryzyk_struktura",
                )

                st.session_state["orchestrator_review"] = analysis_review
                st.session_state["orchestrator_risk_list"] = analysis_risk_list

        review = st.session_state.get("orchestrator_review", "")
        risk_list = st.session_state.get("orchestrator_risk_list", [])

        if review:
            st.markdown(review)

        if risk_list:
            severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
            sorted_risks = sorted(
                risk_list,
                key=lambda r: severity_order.get(r.get("severity", "").upper(), 3)
            )
            rows = []
            for r in sorted_risks:
                rows.append({
                    "Tytuł": r.get("title", ""),
                    "Obszar": r.get("area", ""),
                    "Poziom": r.get("severity", ""),
                    "Opis": r.get("description", ""),
                    "Rekomendacja": r.get("mitigation", ""),
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)

        st.subheader("Wersja umowy po ograniczeniu ryzyk")
        if st.button("Zaproponuj wersję umowy po ograniczeniu ryzyk"):
            current_contract = st.session_state.get("orchestrator_contract_text", "")
            current_risks = st.session_state.get("orchestrator_risk_list", [])
            if not current_contract:
                st.warning("Brak projektu umowy do poprawienia.")
            elif not current_risks:
                st.warning("Brak zidentyfikowanych ryzyk do ograniczenia.")
            else:
                refined_text = ContractRefinerAgent().run(current_contract, current_risks)
                vault.save_document(
                    client_name,
                    "raw",
                    refined_text,
                    "txt",
                    label="orchestrator_umowa_po_ograniczeniu_ryzyk",
                )
                st.session_state["orchestrator_refined_contract_text"] = refined_text

        if st.session_state.get("orchestrator_refined_contract_text", ""):
            st.text_area(
                "Poprawiona wersja umowy",
                value=st.session_state["orchestrator_refined_contract_text"],
                height=300,
                disabled=True,
                key="orchestrator_refined_display",
            )

        st.subheader("Podsumowanie e-mail dla klienta (tryb automatyczny)")
        if st.button("Wygeneruj podsumowanie e-mail dla klienta", key="orchestrator_email_btn"):
            current_contract = st.session_state.get("orchestrator_contract_text", "")
            current_risks = st.session_state.get("orchestrator_risk_list", [])
            if not current_contract:
                st.warning("Brak projektu umowy do podsumowania.")
            else:
                email_result = EmailBriefAgent().run(current_contract, current_risks)
                vault.save_document(
                    client_name,
                    "email",
                    email_result,
                    "md",
                    label="orchestrator_podsumowanie_mailowe",
                )
                st.session_state["orchestrator_email_body"] = email_result

        if st.session_state.get("orchestrator_email_body", ""):
            st.text_area(
                "Treść maila do skopiowania",
                value=st.session_state["orchestrator_email_body"],
                height=300,
                disabled=True,
                key="orchestrator_email_display",
            )


def render_home(client_name: str, vault: VaultManager):
    st.title("LexOrchestra Platform")

    if not client_name:
        st.warning("Aby rozpocząć, wybierz lub utwórz klienta w panelu bocznym.")
        return

    st.markdown(f"Witaj w panelu sprawy dla klienta: **{client_name}**. Wybierz moduł, aby rozpocząć pracę.")

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        if st.button("Asystent główny (Orchestrator)", use_container_width=True):
            st.session_state["tool"] = "ORCHESTRATOR"
            st.rerun()

    with c2:
        if st.button("Analiza i Anonimizacja", use_container_width=True):
            st.session_state["tool"] = "ANALYSIS"
            st.rerun()

    with c3:
        if st.button("Dobór Wzoru (Templates)", use_container_width=True):
            st.session_state["tool"] = "TEMPLATES"
            st.rerun()

    with c4:
        st.button("Due Diligence (Coming Soon)", disabled=True, use_container_width=True)

    st.markdown("---")
    st.subheader("Ostatnie dokumenty klienta")

    recent_docs = vault.list_recent_documents(client_name, limit=10)

    if not recent_docs:
        st.info("Brak jeszcze dokumentów dla tego klienta.")
    else:
        for doc in recent_docs:
            label = DOC_TYPE_LABELS.get(doc["doc_type"], doc["doc_type"])
            expander_title = f"{label} – {doc['filename']} ({doc['timestamp']})"
            with st.expander(expander_title):
                content = Path(doc["path"]).read_text(encoding="utf-8")
                st.text_area("Zawartość", value=content, height=250, disabled=True, key=f"recent_{doc['doc_type']}_{doc['filename']}")


def main():
    vault = VaultManager()
    seed_templates_if_empty()
    if not vault.get_clients():
        seed_demo_clients(vault)
    client_name = render_sidebar(vault)

    if "tool" not in st.session_state:
        st.session_state["tool"] = "HOME"

    if st.session_state["tool"] == "HOME":
        render_home(client_name, vault)

    elif st.session_state["tool"] == "ANALYSIS":
        if st.button("← Wróć do pulpitu"):
            st.session_state["tool"] = "HOME"
            if "orchestrator_last_decision" in st.session_state:
                st.session_state["orchestrator_last_decision"] = None
            st.rerun()

        if client_name:
            render_analysis_module(client_name, vault)
        else:
            st.error("Utracono kontekst klienta. Wybierz go ponownie z menu po lewej.")

    elif st.session_state["tool"] == "ORCHESTRATOR":
        if st.button("← Wróć do pulpitu"):
            st.session_state["tool"] = "HOME"
            if "orchestrator_last_decision" in st.session_state:
                st.session_state["orchestrator_last_decision"] = None
            st.rerun()

        if client_name:
            render_orchestrator_module(client_name, vault)
        else:
            st.error("Brak aktywnego klienta. Wybierz klienta w panelu bocznym.")

    elif st.session_state["tool"] == "TEMPLATES":
        if st.button("← Wróć do pulpitu"):
            st.session_state["tool"] = "HOME"
            if "orchestrator_last_decision" in st.session_state:
                st.session_state["orchestrator_last_decision"] = None
            st.rerun()

        if client_name:
            render_templates_module(client_name, vault)
        else:
            st.error("Brak aktywnego klienta. Wybierz klienta w panelu bocznym.")


if __name__ == "__main__":
    main()
