# 🧬 SNOMED CT Intelligent Explorer

**LLM-Assisted Intelligent Agent for Interactive Exploration and Explainable Navigation of the SNOMED CT Ontology**

A research project that explores the use of **SNOMED CT, BioPortal, Neo4j, BFS-based graph crawling, and an interactive Streamlit interface** to dynamically retrieve and explore relevant portions of the SNOMED CT clinical terminology hierarchy.

---

## 📌 Project Overview

SNOMED CT is a large clinical terminology containing concepts and relationships organized into a complex hierarchical structure.

The goal of this project is to build an intelligent system that can:

- Resolve a clinical concept from a user's query.
- Retrieve the corresponding SNOMED CT concept.
- Dynamically crawl its relevant hierarchical relationships.
- Store the retrieved concepts and relationships in a Neo4j knowledge graph.
- Allow users to interactively explore the retrieved SNOMED CT hierarchy.
- Provide an interface that can later be extended with an LLM for natural-language explanation and intelligent navigation.

The current implementation focuses primarily on the **SNOMED CT graph scraping and knowledge graph construction pipeline**.

---

## 🎯 Current Objectives

The current system is designed to:

1. Search for SNOMED CT concepts.
2. Retrieve concept information from BioPortal.
3. Retrieve hierarchical parent relationships.
4. Dynamically expand the hierarchy using **Breadth-First Search (BFS)**.
5. Control graph expansion using a configurable depth.
6. Store concepts in Neo4j.
7. Store `IS_A` relationships between concepts.
8. Query the resulting knowledge graph.
9. Provide an interactive Streamlit-based visualization/exploration interface.

---

## 🏗️ System Architecture

```text
                 ┌──────────────────────┐
                 │       User           │
                 │  Clinical Concept    │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │  Query Processor     │
                 │  Concept Resolution  │
                 └──────────┬───────────┘
                            │
                 ┌──────────▼───────────┐
                 │      BioPortal       │
                 │   SNOMED CT API      │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │    BFS Crawler       │
                 │ Hierarchy Expansion  │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │       Neo4j          │
                 │   Knowledge Graph    │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │    Streamlit UI      │
                 │ Interactive Explorer │
                 └──────────────────────┘
