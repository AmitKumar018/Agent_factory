# #as per milestone-1
# import structlog

# logger = structlog.get_logger()


# async def seed_patterns() -> None:
#     """
#     Seed default patterns into the database.
#     M1 stub — no patterns to seed yet. Added in M3 (Agent Patterns milestone).
#     """
#     logger.info(
#         "pattern_seed_complete",
#         patterns_added=0,
#         message="M1 stub — no patterns seeded"
#     )









#as per milestone-2

"""
Idempotent seeder for the 10 canonical agentic design patterns.
Called once at application startup; safe to call on every restart.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from app.patterns.schemas import PatternCreate
from app.patterns import service as pattern_service

CANONICAL_PATTERNS: list[dict] = [
    {
        "name": "ReAct",
        "intent": (
            "ReAct (Reasoning + Acting) solves the problem of agents that either reason "
            "without acting (pure chain-of-thought) or act without reasoning (blind tool use). "
            "It interleaves thought traces with tool calls so the agent can dynamically adjust "
            "its plan based on real-world feedback at each step."
        ),
        "structure": (
            "Single agent loop: Thought ? Action (tool call) ? Observation ? repeat. "
            "Components: LLM backbone, ToolNode, message history, stop condition."
        ),
        "when_to_use": (
            "Tasks requiring iterative information gathering (web search, code execution, "
            "database lookup). The answer is not known upfront and depends on intermediate "
            "observations. Tool results meaningfully change the next reasoning step."
        ),
        "when_not_to_use": (
            "Tasks fully answerable from model knowledge alone. "
            "Latency-critical paths where multi-hop tool calls are too slow. "
            "Highly structured output generation where a planner-executor is cleaner."
        ),
        "prerequisites": "Tool definitions, ToolNode, bounded iteration counter to prevent infinite loops.",
        "references": "Yao et al. 2022 — https://arxiv.org/abs/2210.03629",
        "tags": ["single-agent", "tool-use", "reasoning", "retrieval"],
    },
    {
        "name": "Reflection",
        "intent": (
            "Reflection addresses the tendency of LLMs to produce first-draft outputs that "
            "contain errors, omissions, or style violations. A critic pass evaluates the draft "
            "against an explicit rubric and the generator revises, producing higher-quality "
            "output without human intervention."
        ),
        "structure": (
            "Two-node cycle: Generator ? Critic ? (pass/fail edge) ? Generator. "
            "Critic outputs structured feedback; Generator incorporates feedback in next draft. "
            "Bounded by max_iterations state counter."
        ),
        "when_to_use": (
            "Document generation, code writing, planning artifacts where quality matters "
            "more than latency. A clear, scorable rubric exists. Output length justifies "
            "multiple passes."
        ),
        "when_not_to_use": (
            "Simple lookup or classification tasks. Tight latency budgets. "
            "No clear rubric — the critic will give vague feedback that doesn't converge."
        ),
        "prerequisites": "Rubric definition, structured critic output (Pydantic), iteration counter in state.",
        "references": "Shinn et al. 2023 Reflexion — https://arxiv.org/abs/2303.11366",
        "tags": ["single-agent", "self-critique", "quality", "iteration"],
    },
    {
        "name": "Planner-Executor",
        "intent": (
            "Planner-Executor separates the concern of deciding what to do (planning) from "
            "doing it (execution), enabling complex multi-step tasks to be broken into "
            "ordered, verifiable sub-tasks that are executed sequentially with full context."
        ),
        "structure": (
            "Two-phase graph: Planner node produces a structured task list ? Executor loop "
            "consumes tasks one at a time, writes results back to state, advances task pointer. "
            "Optional re-planning edge if a task fails."
        ),
        "when_to_use": (
            "Long-horizon tasks with 5+ sequential steps. Steps have dependencies. "
            "Each step produces an artifact consumed by later steps. "
            "User needs to inspect and approve the plan before execution."
        ),
        "when_not_to_use": (
            "Tasks with fewer than 3 steps where overhead isn't justified. "
            "Highly dynamic tasks where the plan becomes stale after step 1."
        ),
        "prerequisites": "Structured task schema (Pydantic), task-pointer in state, FileStore for artifacts.",
        "references": "Wei et al. Plan-and-Solve — https://arxiv.org/abs/2305.04091",
        "tags": ["multi-step", "planning", "orchestration", "sequential"],
    },
    {
        "name": "Multi-Agent Debate",
        "intent": (
            "Multi-Agent Debate improves factual accuracy and reduces individual LLM bias by "
            "having multiple independent agent instances argue positions and then synthesize "
            "a consensus answer, effectively peer-reviewing each other's output."
        ),
        "structure": (
            "N debate agents (same or different models) receive the same question, produce "
            "independent responses, then share responses with all peers for a rebuttal round. "
            "A judge/synthesizer node produces the final answer."
        ),
        "when_to_use": (
            "High-stakes factual questions. Situations where a single LLM is prone to "
            "confident hallucination. Architecture or design decisions where diverse perspectives matter."
        ),
        "when_not_to_use": (
            "Cost-sensitive tasks (N × LLM calls per question). "
            "Tasks with a single objectively correct answer retrievable from a tool. "
            "Latency-sensitive pipelines."
        ),
        "prerequisites": "Multiple agent instantiations, shared message bus, judge node, round counter.",
        "references": "Du et al. 2023 — https://arxiv.org/abs/2305.14325",
        "tags": ["multi-agent", "debate", "accuracy", "consensus"],
    },
    {
        "name": "Router",
        "intent": (
            "A Router classifies an incoming request and dispatches it to the specialist "
            "sub-graph or agent best equipped to handle it, preventing every request from "
            "incurring the cost and latency of the most complex path."
        ),
        "structure": (
            "Classifier node reads request features (length, keywords, metadata) and emits "
            "a route label. Conditional edge maps label to target node or subgraph. "
            "Each target handles its own completion."
        ),
        "when_to_use": (
            "Workloads with clearly distinguishable complexity tiers (simple vs. complex). "
            "Multiple specialist agents with non-overlapping competencies. "
            "Cost optimisation is important."
        ),
        "when_not_to_use": (
            "Homogeneous inputs where routing adds latency with no benefit. "
            "Fewer than 2 distinct handling paths."
        ),
        "prerequisites": "Classification prompt or ML classifier, conditional_edge wiring in LangGraph.",
        "references": "LangGraph routing documentation — https://langchain-ai.github.io/langgraph/",
        "tags": ["routing", "conditional", "orchestration", "efficiency"],
    },
    {
        "name": "RAG",
        "intent": (
            "Retrieval-Augmented Generation grounds LLM responses in a private or up-to-date "
            "corpus by retrieving relevant document chunks at query time and injecting them "
            "into the prompt, reducing hallucination and enabling domain-specific knowledge "
            "without fine-tuning."
        ),
        "structure": (
            "Query ? Embedder ? Vector store retrieval (top-k chunks) ? Prompt construction "
            "(system + retrieved context + question) ? LLM ? Answer. "
            "Optionally: re-ranker between retrieval and prompt construction."
        ),
        "when_to_use": (
            "Domain-specific Q&A. Document summarisation. Any task where the LLM's training "
            "data is stale or the corpus is private. Pattern KB lookups."
        ),
        "when_not_to_use": (
            "Real-time data where vector store freshness cannot be guaranteed. "
            "Tasks where retrieved chunks would exceed context window reliably."
        ),
        "prerequisites": "Embedding model, vector store (ChromaDB), chunking strategy, project_id metadata filter.",
        "references": "Lewis et al. 2020 — https://arxiv.org/abs/2005.11401",
        "tags": ["retrieval", "grounding", "knowledge-base", "rag"],
    },
    {
        "name": "Tool-Use",
        "intent": (
            "Tool-Use extends an LLM's capability beyond text generation by giving it access "
            "to deterministic functions (API calls, code execution, database queries) it can "
            "invoke when its parametric knowledge is insufficient or unreliable."
        ),
        "structure": (
            "Agent node emits a tool_call; ToolNode executes the function and returns a "
            "ToolMessage; agent resumes with the result. Error messages are returned as "
            "ToolMessages (not raised) so the agent can self-correct."
        ),
        "when_to_use": (
            "Any task requiring real-time data, computation, or side effects. "
            "Math, code execution, search, file I/O, external APIs."
        ),
        "when_not_to_use": (
            "Tasks solvable from model knowledge alone. Environments where external calls "
            "are not allowed or introduce unacceptable latency."
        ),
        "prerequisites": "Tool schemas (standard JSON Schema / Function format), ToolNode, error-as-message handling.",
        "references": "LLM Function calling and tool-use standards",
        "tags": ["tool-use", "function-calling", "single-agent", "extensibility"],
    },
    {
        "name": "Hierarchical Agents",
        "intent": (
            "Hierarchical Agents tackle problems too large or complex for a single agent by "
            "decomposing them into sub-problems, assigning each to a specialist sub-agent "
            "or sub-graph, and having a supervisor synthesise the results — mirroring how "
            "human organisations delegate and consolidate work."
        ),
        "structure": (
            "Supervisor node decides which worker (sub-graph) to invoke next. "
            "Workers execute and return structured results to supervisor. "
            "Supervisor loops until all sub-tasks are complete, then synthesises."
        ),
        "when_to_use": (
            "Large, multi-domain problems. Mixed specialist requirements (researcher + coder "
            "+ reviewer). Tasks where parallel delegation speeds up wall-clock time."
        ),
        "when_not_to_use": (
            "Simple single-domain tasks. Teams of agents with high coordination overhead "
            "that outweighs the parallelism benefit."
        ),
        "prerequisites": "Supervisor prompt, worker subgraphs with typed I/O, LangGraph subgraph composition.",
        "references": "LangGraph multi-agent supervisor example — https://langchain-ai.github.io/langgraph/tutorials/multi_agent/",
        "tags": ["multi-agent", "supervisor", "hierarchical", "delegation"],
    },
    {
        "name": "Critic-Refine (Reflexion)",
        "intent": (
            "Critic-Refine (Reflexion) adds episodic memory to the Reflection pattern: "
            "after each failed attempt, a verbal reflection is stored in long-term memory "
            "and injected into the next attempt's context, enabling the agent to avoid "
            "repeating the same mistakes across episodes."
        ),
        "structure": (
            "Actor (generator) ? Evaluator (pass/fail + score) ? Reflector (verbal critique "
            "stored to memory) ? Actor (reads memory + retries). "
            "Memory persists across episode boundaries."
        ),
        "when_to_use": (
            "Multi-attempt tasks where early failures contain learnable signal. "
            "Code generation with test harnesses. Planning with acceptance criteria. "
            "Any setting where max_attempts > 2."
        ),
        "when_not_to_use": (
            "Single-attempt tasks. Tasks where past failures are irrelevant to the next attempt. "
            "Cost-sensitive pipelines where reflection overhead is unjustified."
        ),
        "prerequisites": "In-memory or persistent memory store, structured evaluator output, Reflector prompt.",
        "references": "Shinn et al. 2023 Reflexion — https://arxiv.org/abs/2303.11366",
        "tags": ["reflection", "memory", "self-critique", "iteration", "code-generation"],
    },
    {
        "name": "Map-Reduce / Parallel Workers",
        "intent": (
            "Map-Reduce addresses throughput bottlenecks in processing large collections "
            "by fanning out work across parallel worker invocations (map) and then "
            "merging their results into a single output (reduce), dramatically cutting "
            "wall-clock time for embarrassingly parallel tasks."
        ),
        "structure": (
            "Splitter node partitions input into N chunks ? parallel Send() dispatches each "
            "chunk to a worker node ? reducer node collects all worker outputs via an "
            "accumulator list reducer in state schema."
        ),
        "when_to_use": (
            "Processing many independent items (documents, tasks, search results). "
            "Research branches that don't depend on each other. "
            "Any pipeline where sequential processing is the bottleneck."
        ),
        "when_not_to_use": (
            "Items with sequential dependencies (each item needs the previous result). "
            "Small collections where fan-out overhead exceeds sequential cost."
        ),
        "prerequisites": "LangGraph Send() API, accumulator reducer on state list field, fan-in node.",
        "references": "LangGraph map-reduce how-to — https://langchain-ai.github.io/langgraph/how-tos/map-reduce/",
        "tags": ["parallelization", "map-reduce", "throughput", "fan-out"],
    },
]


async def seed_patterns(db: AsyncSession) -> None:
    """
    Insert canonical patterns if they don't already exist.
    Idempotent: safe to call on every app startup.
    """
    seeded = 0
    skipped = 0

    for entry in CANONICAL_PATTERNS:
        existing = await pattern_service.get_pattern_by_name(db, entry["name"])
        if existing:
            pattern_service._upsert_to_chroma(existing)
            skipped += 1
            continue

        await pattern_service.create_pattern(
            db,
            PatternCreate(**entry),
            source="seed",
        )
        seeded += 1

    print(f"[seed] Patterns — seeded: {seeded}, already present: {skipped}")
