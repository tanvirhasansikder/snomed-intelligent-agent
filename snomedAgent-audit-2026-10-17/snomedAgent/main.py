import logging

import streamlit as st

from agent.query_processor import QueryProcessor
from crawler.bioportal_client import BioPortalConfigurationError
from database.neo4j_connection import Neo4jConfigurationError
from ui.view_model import (
    ancestor_note,
    count_label,
    coverage_notice,
    error_details,
    error_message,
    escape,
    extract_concept_id,
    extract_concept_name,
    extract_relationships,
    normalize_concepts,
    redact,
    resolution_note,
    safe_list,
)

logging.basicConfig(level=logging.WARNING)


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="SNOMED CT Intelligent Explorer",
    page_icon="🧬",
    layout="wide"
)


# ============================================================
# CUSTOM CSS
# ============================================================

st.markdown("""
<style>

    /* ========================================================
       MAIN APPLICATION
       ======================================================== */

    .stApp {
        background-color: #000000;
    }

    /* Keep normal Streamlit text light on the black background.
       IMPORTANT: this does NOT target arbitrary div/span elements,
       so text inside our white cards can have its own color. */

    .stApp p,
    .stApp label {
        color: #f8fafc;
    }


    /* ========================================================
       MAIN TITLE
       ======================================================== */

    .main-title {
        color: #ffffff !important;
        font-size: 2.6rem !important;
        font-weight: 800 !important;
        line-height: 1.2 !important;
    }

    h1 {
        color: #ffffff !important;
        font-size: 2.4rem !important;
    }


    /* ========================================================
       WHITE INFORMATION / RESULT BOXES
       ======================================================== */

    .result-box {
        background-color: #ffffff;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 15px;
        border: 1px solid #e5e7eb;
        color: #1f2937 !important;
    }

    .result-box h3,
    .result-box h4,
    .result-box p,
    .result-box span,
    .result-box div {
        color: #1f2937 !important;
    }


    /* ========================================================
       CONCEPT CARDS
       ======================================================== */

    .concept-card {
        padding: 1.2rem;
        border-radius: 12px;
        border: 1px solid #e5e7eb;
        background-color: #ffffff;
        margin-bottom: 1rem;
        color: #1f2937 !important;
    }

    .concept-card strong {
        color: #111827 !important;
    }


    /* ========================================================
       CONCEPT NAME
       ======================================================== */

    .concept-name {
        color: #111827 !important;
        font-size: 1.35rem;
        font-weight: 700;
    }


    /* ========================================================
       SNOMED ID
       ======================================================== */

    .concept-id {
        color: #4b5563 !important;
        font-family: monospace;
        font-size: 14px;
    }


    /* ========================================================
       PARENT / CHILD NAMES
       ======================================================== */

    .relation-name {
        color: #111827 !important;
        font-size: 17px;
        font-weight: 600;
    }

    .relation-id {
        color: #6b7280 !important;
        font-size: 13px;
    }


    /* ========================================================
       STATISTIC CARDS
       ======================================================== */

    .stat-card {
        padding: 1rem;
        border-radius: 10px;
        border: 1px solid #e5e7eb;
        background-color: #ffffff;
        text-align: center;
        color: #1f2937 !important;
    }

    .stat-number {
        font-size: 1.7rem;
        font-weight: 700;
        color: #111827 !important;
    }

    .stat-label {
        color: #4b5563 !important;
        font-size: 0.9rem;
    }


    /* ========================================================
       STREAMLIT METRIC CARDS
       ======================================================== */

    [data-testid="stMetric"] {
        background-color: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        padding: 15px;
    }

    [data-testid="stMetricLabel"] {
        color: #4b5563 !important;
    }

    [data-testid="stMetricValue"] {
        color: #111827 !important;
    }


    /* ========================================================
       TEXT INPUT
       ======================================================== */

    .stTextInput input {
        background-color: #ffffff !important;
        color: #111827 !important;
        border: 1px solid #d1d5db;
    }

    .stTextInput input::placeholder {
        color: #9ca3af !important;
    }


    /* ========================================================
       BUTTONS
       ======================================================== */

    .stButton > button {
        color: #ffffff !important;
        background-color: #7f1d1d;
        border: 1px solid #991b1b;
        border-radius: 8px;
        font-weight: 600;
    }

    .stButton > button:hover {
        background-color: #991b1b;
        color: #ffffff !important;
        border-color: #b91c1c;
    }


    /* ========================================================
       EXPANDERS
       ======================================================== */

    [data-testid="stExpander"] {
        background-color: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 10px;
    }

    [data-testid="stExpander"] summary {
        color: #111827 !important;
    }

    [data-testid="stExpander"] summary p {
        color: #111827 !important;
    }


    /* ========================================================
       CODE / HIERARCHY BLOCKS
       ======================================================== */

    code {
        color: #111827 !important;
        background-color: #f3f4f6 !important;
    }


    /* ========================================================
       SECTION HEADERS
       ======================================================== */

    .section-header {
        color: #ffffff !important;
        font-size: 1.4rem;
        font-weight: 650;
        margin-top: 1rem;
        margin-bottom: 1rem;
    }


    /* ========================================================
       SUBTITLE
       ======================================================== */

    .subtitle {
        color: #f3f4f6 !important;
        font-size: 1.05rem;
        margin-bottom: 2rem;
    }


    /* ========================================================
       PIPELINE
       ======================================================== */

    .pipeline-step {
        padding: 0.45rem 0;
        font-weight: 500;
        color: #ffffff !important;
    }


    /* ========================================================
       RELATIONSHIP CARDS
       ======================================================== */

    .relationship-card {
        padding: 0.9rem 1.1rem;
        border-radius: 10px;
        border: 1px solid #e5e7eb;
        background-color: #ffffff;
        margin-bottom: 0.7rem;
        color: #1f2937 !important;
    }

    .relationship-card strong {
        color: #111827 !important;
    }


    /* ========================================================
       SIDEBAR
       ======================================================== */

    section[data-testid="stSidebar"] {
        background-color: #0f0f0f;
    }

    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3,
    section[data-testid="stSidebar"] h4,
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] span {
        color: #ffffff !important;
    }


    /* ========================================================
       SIDEBAR SLIDER
       ======================================================== */

    section[data-testid="stSidebar"] [data-testid="stSlider"] {
        color: #ffffff !important;
    }


    /* ========================================================
       STREAMLIT INFO / WARNING / SUCCESS MESSAGES
       ======================================================== */

    [data-testid="stAlert"] p {
        color: inherit !important;
    }


    /* ========================================================
       FOOTER
       ======================================================== */

    .footer {
        color: #fecaca !important;
        text-align: center;
        margin-top: 40px;
        font-size: 13px;
    }

</style>
""", unsafe_allow_html=True)


# ============================================================
# HEADER
# ============================================================

st.markdown(
    '<div class="main-title">🧬 SNOMED CT Intelligent Explorer</div>',
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="subtitle">
    LLM-Assisted Intelligent Agent for Interactive Exploration and
    Explainable Navigation of the SNOMED CT Ontology
    </div>
    """,
    unsafe_allow_html=True,
)

st.caption(
    "Prototype status: concept resolution, hierarchy retrieval and display "
    "are deterministic. LLM-generated explanations are planned and not yet "
    "implemented."
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("⚙️ Exploration Settings")

    expansion_depth = st.slider(
        "Expansion Depth",
        min_value=1,
        max_value=5,
        value=2,
        help="Number of SNOMED CT hierarchy levels to dynamically retrieve.",
    )

    fetch_children = st.checkbox(
        "Retrieve direct children from BioPortal",
        value=True,
        help=(
            "When off, only children already stored in the local graph "
            "are shown, and an empty list does not mean there are none."
        ),
    )

    st.divider()

    st.markdown("### 🔄 System Pipeline")

    st.markdown(
        """
        <div class="pipeline-step">👤 User Query</div>
        <div style="text-align:center; color:#ffffff;">↓</div>

        <div class="pipeline-step">🔎 Concept Resolution</div>
        <div style="text-align:center; color:#ffffff;">↓</div>

        <div class="pipeline-step">🌐 BioPortal API</div>
        <div style="text-align:center; color:#ffffff;">↓</div>

        <div class="pipeline-step">🔄 Dynamic BFS Expansion</div>
        <div style="text-align:center; color:#ffffff;">↓</div>

        <div class="pipeline-step">🗄️ Neo4j Knowledge Graph</div>
        <div style="text-align:center; color:#ffffff;">↓</div>

        <div class="pipeline-step">📊 Hierarchy Result + Coverage</div>
        """,
        unsafe_allow_html=True,
    )

    st.divider()

    st.markdown("### 💡 Example Concepts")

    st.markdown(
        """
        - Pneumonia
        - Diabetes mellitus
        - Asthma
        - Heart disease
        - Stroke
        """
    )

    st.divider()

    st.caption("CSE499 Research Project")


# ============================================================
# QUERY SECTION
# ============================================================

st.subheader("🔎 Explore SNOMED CT")

st.write(
    "Enter a clinical concept to dynamically retrieve and explore "
    "its relevant SNOMED CT hierarchy."
)


query = st.text_input(
    "Concept",
    placeholder="e.g. Asthma",
    label_visibility="collapsed",
)


explore_button = st.button(
    "🚀 Explore Concept",
    type="primary",
    use_container_width=True,
)


# ============================================================
# HELPER FUNCTIONS
# ============================================================
# Moved to ui/view_model.py (unchanged) so they can be unit-tested
# without starting Streamlit. Imported at the top of this file.


# ============================================================
# PROCESS QUERY
# ============================================================

if explore_button:

    if not query.strip():

        st.warning("Please enter a SNOMED CT concept.")

    else:

        try:

            with st.spinner(
                "Resolving concept and dynamically expanding SNOMED CT..."
            ):

                processor = QueryProcessor(
                    expansion_depth=expansion_depth,
                    fetch_children=fetch_children,
                )

                result = processor.process_query(
                    query.strip()
                )

        except (Neo4jConfigurationError, BioPortalConfigurationError) as error:

            st.session_state.pop("query_result", None)
            st.error(error_message({"error_type": "configuration_error"}))
            st.caption(redact(error))

        except Exception as error:

            st.session_state.pop("query_result", None)
            st.error(
                "An error occurred while processing the query."
            )
            st.exception(error)

        else:

            st.session_state["last_query"] = query.strip()

            if result.get("success"):

                st.session_state["query_result"] = result

                st.success(
                    "Concept exploration completed."
                )

            else:

                st.session_state.pop("query_result", None)

                st.error(error_message(result))

                with st.expander("Details"):
                    st.write(error_details(result))


# ============================================================
# DISPLAY RESULTS
# ============================================================

if "query_result" in st.session_state:

    result = st.session_state["query_result"]

    st.divider()

    st.subheader("📊 Exploration Result")


    # --------------------------------------------------------
    # EXTRACT RESULT DATA
    # --------------------------------------------------------

    concept_id = extract_concept_id(result)

    concept_name = extract_concept_name(result)

    parents = normalize_concepts(
        result.get("parents", [])
        if isinstance(result, dict)
        else []
    )

    children = normalize_concepts(
        result.get("children", [])
        if isinstance(result, dict)
        else []
    )

    ancestors = normalize_concepts(
        result.get("ancestors", [])
        if isinstance(result, dict)
        else []
    )

    relationships = extract_relationships(result)


    # ========================================================
    # CONCEPT CARD
    # ========================================================

    st.markdown(
        f"""
        <div class="concept-card">

        <div class="concept-name">
        🧬 {escape(concept_name)}
        </div>

        <div class="concept-id">
        SNOMED CT ID: {escape(concept_id)}
        </div>

        </div>
        """,
        unsafe_allow_html=True,
    )

    note = resolution_note(result)

    if note:
        st.caption(note)

    for warning_text in safe_list(result.get("warnings")):
        st.warning(warning_text)

    candidates = normalize_concepts(result.get("candidates", []))

    if (result.get("resolution") or {}).get("ambiguous") and candidates:

        with st.expander("Other BioPortal matches"):

            for candidate in candidates:
                st.write(f"{candidate['name']} ({candidate['concept_id']})")


    # ========================================================
    # STATISTICS
    # ========================================================

    col1, col2, col3 = st.columns(3)


    with col1:

        st.markdown(
            f"""
            <div class="stat-card">

            <div class="stat-number">
            {len(parents)}
            </div>

            <div class="stat-label">
            {escape(count_label(result, "parents"))}
            </div>

            </div>
            """,
            unsafe_allow_html=True,
        )


    with col2:

        st.markdown(
            f"""
            <div class="stat-card">

            <div class="stat-number">
            {len(children)}
            </div>

            <div class="stat-label">
            {escape(count_label(result, "children"))}
            </div>

            </div>
            """,
            unsafe_allow_html=True,
        )


    with col3:

        st.markdown(
            f"""
            <div class="stat-card">

            <div class="stat-number">
            {len(ancestors)}
            </div>

            <div class="stat-label">
            Ancestors Retrieved
            </div>

            </div>
            """,
            unsafe_allow_html=True,
        )


    # ========================================================
    # DIRECT PARENTS
    # ========================================================

    st.divider()

    st.markdown(
        '<div class="section-header">⬆️ Direct Parents</div>',
        unsafe_allow_html=True,
    )


    for parent in parents:

        st.markdown(
            f"""
            <div class="concept-card">

            <strong>{escape(parent["name"])}</strong>

            <br>

            <span class="concept-id">
            SNOMED CT ID: {escape(parent["concept_id"])}
            </span>

            </div>
            """,
            unsafe_allow_html=True,
        )

    notice = coverage_notice(result, "parents")

    if notice:
        getattr(st, notice[0])(notice[1])


    # ========================================================
    # DIRECT CHILDREN
    # ========================================================

    st.divider()

    st.markdown(
        '<div class="section-header">⬇️ Direct Children</div>',
        unsafe_allow_html=True,
    )


    for child in children:

        st.markdown(
            f"""
            <div class="concept-card">

            <strong>{escape(child["name"])}</strong>

            <br>

            <span class="concept-id">
            SNOMED CT ID: {escape(child["concept_id"])}
            </span>

            </div>
            """,
            unsafe_allow_html=True,
        )

    notice = coverage_notice(result, "children")

    if notice:
        getattr(st, notice[0])(notice[1])


    # ========================================================
    # RELATIONSHIPS
    # ========================================================

    if relationships:

        st.divider()

        st.markdown(
            '<div class="section-header">🔗 Relationships</div>',
            unsafe_allow_html=True,
        )

        for relationship in relationships:

            if isinstance(relationship, dict):

                relationship_type = (
                    relationship.get("type")
                    or relationship.get("relationship")
                    or relationship.get("name")
                    or "Relationship"
                )

                source = (
                    relationship.get("source")
                    or relationship.get("source_name")
                    or concept_name
                )

                target = (
                    relationship.get("target")
                    or relationship.get("target_name")
                    or "Unknown"
                )

                st.markdown(
                    f"""
                    <div class="relationship-card">

                    <strong>{escape(source)}</strong>
                    &nbsp; → &nbsp;
                    <strong>{escape(relationship_type)}</strong>
                    &nbsp; → &nbsp;
                    <strong>{escape(target)}</strong>

                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            else:

                st.write(relationship)


    # ========================================================
    # HIERARCHY SUMMARY
    # ========================================================

    st.divider()

    st.markdown(
        '<div class="section-header">🌳 Hierarchy Summary</div>',
        unsafe_allow_html=True,
    )


    if parents:

        st.markdown(
            f"""
            <div style="
                color:#ffffff;
                font-weight:700;
                font-size:1.05rem;
                margin-bottom:0.5rem;
            ">
            {escape(concept_name)}
            </div>
            """,
            unsafe_allow_html=True,
        )

        for index, parent in enumerate(parents):

            prefix = "└──" if index == len(parents) - 1 else "├──"

            st.code(
                f"{prefix} {parent['name']} ({parent['concept_id']})"
            )

    else:

        st.info("No parent hierarchy to summarise (see the Direct Parents section).")

    if ancestors:

        st.markdown("**Ancestors in the retrieved subgraph**")

        st.write(
            ", ".join(
                f"{ancestor['name']} ({ancestor['concept_id']})"
                for ancestor in ancestors
            )
        )

    note = ancestor_note(result)

    if note:
        st.caption(note)


    # ========================================================
    # RAW RESULT
    # ========================================================

    st.divider()

    with st.expander("🔍 View Raw Query Result"):

        st.json(result)


# ============================================================
# INITIAL INFORMATION
# ============================================================

else:

    st.info(
        "👆 Enter a SNOMED CT concept above to begin exploring "
        "the ontology."
    )


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "SNOMED CT Intelligent Explorer • CSE499 Research Project"
)