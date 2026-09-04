"""
Module implementing the Reflection pattern for self-critique loops in the planning process.
"""

from typing import List, Dict, Any
import logging

# Constants for generator and critic prompts
GENERATOR_PROMPT = (
    "Role: Architecture and Task Plan Generator\n"
    "Instructions: Generate a comprehensive architecture and task plan based on the provided requirements. "
    "Ensure the output is structured as a JSON object with keys 'architecture' and 'tasks'. "
    "Constraints: Follow the specification strictly and avoid any assumptions not grounded in the requirements.\n"
    "Input: {requirements}\n"
    "Output Format: JSON\n"
)

CRITIC_PROMPT = (
    "Role: Plan Critic\n"
    "Instructions: Evaluate the generated architecture and task plan for correctness, completeness, and alignment with the specification. "
    "Identify any deviations or missing elements. Provide feedback in a structured format.\n"
    "Constraints: Ensure feedback is clear and actionable, and avoid any assumptions not grounded in the requirements.\n"
    "Input: {plan}\n"
    "Output Format: JSON\n"
)

# Configuration for maximum iterations
MAX_ITERATIONS = 5

def generate_plan(requirements: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generates an architecture and task plan based on the given requirements.

    :param requirements: A dictionary containing the project requirements.
    :return: A dictionary representing the generated plan.
    """
    # Placeholder for plan generation logic
    return {"architecture": "Generated architecture based on requirements", "tasks": "Generated tasks based on requirements"}

def critique_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    Critiques the generated plan for correctness, completeness, and alignment with the specification.

    :param plan: A dictionary representing the generated plan.
    :return: A dictionary containing the critique results with a pass/fail status and a list of issues.
    """
    # Placeholder for critique logic
    passed = True
    issues = []

    # Example critique logic
    if "architecture" not in plan or "tasks" not in plan:
        passed = False
        issues.append("Plan is missing architecture or tasks.")

    return {"passed": passed, "issues": issues}

def reflection_loop(requirements: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes the reflection loop to generate and critique plans until they meet the criteria or max iterations are reached.

    :param requirements: A dictionary containing the project requirements.
    :return: The final approved plan.
    """
    logging.info("Starting reflection loop for plan generation.")
    for iteration in range(MAX_ITERATIONS):
        logging.info(f"Iteration {iteration + 1} of {MAX_ITERATIONS}.")
        plan = generate_plan(requirements)
        critique = critique_plan(plan)

        if critique["passed"]:
            logging.info("Plan passed critique.")
            return plan
        else:
            logging.warning(f"Plan failed critique with issues: {critique['issues']}")

    logging.error("Max iterations reached without passing critique.")
    raise RuntimeError("Failed to generate a valid plan within the maximum iterations.")

if __name__ == "__main__":
    # Example usage
    example_requirements = {"requirement": "Example requirement"}
    try:
        final_plan = reflection_loop(example_requirements)
        logging.info(f"Final approved plan: {final_plan}")
    except RuntimeError as e:
        logging.error(str(e))