import streamlit as st
from agent.query_processor import QueryProcessor


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

        <div class="pipeline-step">📊 Explainable Result</div>
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

def safe_list(value):
    """
    Make sure a result field is always returned as a list.
    """
    if value is None:
        return []

    if isinstance(value, list):
        return value

    return []


def extract_concept_id(result):
    """
    Robustly extract the SNOMED CT concept ID.

    Supports several possible result structures.
    """

    if not isinstance(result, dict):
        return "Unknown"

    # Most likely locations
    possible_keys = [
        "concept_id",
        "id",
        "snomed_id",
        "snomed_ct_id",
        "conceptId",
    ]

    for key in possible_keys:
        value = result.get(key)

        if value not in [None, "", "Unknown"]:
            return str(value)

    # Nested concept object
    concept = result.get("concept")

    if isinstance(concept, dict):

        for key in possible_keys:

            value = concept.get(key)

            if value not in [None, "", "Unknown"]:
                return str(value)

    return "Unknown"


def extract_concept_name(result):
    """
    Robustly extract the concept name.
    """

    if not isinstance(result, dict):
        return "Unknown"

    possible_keys = [
        "concept_name",
        "name",
        "preferred_label",
        "label",
    ]

    for key in possible_keys:

        value = result.get(key)

        if value not in [None, "", "Unknown"]:
            return str(value)

    concept = result.get("concept")

    if isinstance(concept, dict):

        for key in possible_keys:

            value = concept.get(key)

            if value not in [None, "", "Unknown"]:
                return str(value)

    return "Unknown"


def extract_relationships(result):
    """
    Extract relationship information if available.
    """

    if not isinstance(result, dict):
        return []

    relationships = result.get("relationships")

    if isinstance(relationships, list):
        return relationships

    return []


def normalize_concepts(items):
    """
    Normalize parent/child concept objects.
    """

    normalized = []

    if not isinstance(items, list):
        return normalized

    seen = set()

    for item in items:

        if isinstance(item, dict):

            concept_id = (
                item.get("concept_id")
                or item.get("id")
                or item.get("snomed_id")
                or item.get("snomed_ct_id")
                or "Unknown"
            )

            name = (
                item.get("name")
                or item.get("concept_name")
                or item.get("preferred_label")
                or item.get("label")
                or "Unknown"
            )

        else:

            concept_id = "Unknown"
            name = str(item)

        unique_key = f"{concept_id}|{name}"

        if unique_key in seen:
            continue

        seen.add(unique_key)

        normalized.append(
            {
                "concept_id": str(concept_id),
                "name": str(name),
            }
        )

    return normalized


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
                    expansion_depth=expansion_depth
                )

                result = processor.process_query(
                    query.strip()
                )

            st.session_state["query_result"] = result
            st.session_state["last_query"] = query.strip()

            st.success(
                "Concept exploration completed successfully."
            )

        except Exception as error:

            st.error(
                "An error occurred while processing the query."
            )

            st.exception(error)


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

    relationships = extract_relationships(result)


    # ========================================================
    # CONCEPT CARD
    # ========================================================

    st.markdown(
        f"""
        <div class="concept-card">

        <div class="concept-name">
        🧬 {concept_name}
        </div>

        <div class="concept-id">
        SNOMED CT ID: {concept_id}
        </div>

        </div>
        """,
        unsafe_allow_html=True,
    )


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
            Direct Parents
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
            Direct Children
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
            {len(parents) + len(children)}
            </div>

            <div class="stat-label">
            Direct Relationships
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


    if parents:

        for parent in parents:

            st.markdown(
                f"""
                <div class="concept-card">

                <strong>{parent["name"]}</strong>

                <br>

                <span class="concept-id">
                SNOMED CT ID: {parent["concept_id"]}
                </span>

                </div>
                """,
                unsafe_allow_html=True,
            )

    else:

        st.info(
            "No direct parents found in the currently loaded graph."
        )


    # ========================================================
    # DIRECT CHILDREN
    # ========================================================

    st.divider()

    st.markdown(
        '<div class="section-header">⬇️ Direct Children</div>',
        unsafe_allow_html=True,
    )


    if children:

        for child in children:

            st.markdown(
                f"""
                <div class="concept-card">

                <strong>{child["name"]}</strong>

                <br>

                <span class="concept-id">
                SNOMED CT ID: {child["concept_id"]}
                </span>

                </div>
                """,
                unsafe_allow_html=True,
            )

    else:

        st.info(
            "No direct children found in the currently loaded graph."
        )


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

                    <strong>{source}</strong>
                    &nbsp; → &nbsp;
                    <strong>{relationship_type}</strong>
                    &nbsp; → &nbsp;
                    <strong>{target}</strong>

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
            {concept_name}
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

        st.info("No hierarchy information available.")


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