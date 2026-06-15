"""Agent definition for the bidi-workshop."""

from google.adk.agents import Agent
from google.adk.tools import google_search

import os
from datetime import datetime
from google.adk.agents.llm_agent import Agent
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.adk.tools.load_memory_tool import load_memory_tool

from .todo import todo_agent
from .txn import txn_agent

# Callback for persistent memory storage
async def auto_save_session_to_memory_callback(callback_context):
    await callback_context._invocation_context.memory_service.add_session_to_memory(
        callback_context._invocation_context.session)
    
# Define the agent
agent = Agent(
    name="workshop_agent",
    model="gemini-live-2.5-flash-native-audio",
    instruction="""You are a helpful AI assistant that can help with tasks, transactions, and search for information.

    You have access to two specialist agents:

    **Todo Specialist** — manages the user's task list:
    - Delegate to this agent when the user wants to create, view, complete, or delete tasks.
    - It provides an interactive form interface for creating tasks.

    **Transaction Specialist** — manages financial transactions:
    - Delegate to this agent when the user wants to view, search, or create transactions.
    - It provides an interactive form interface for creating transactions.
    - Always pass the account_id if the user has provided one.

    You can also use Google Search to find current information.
    Keep your responses concise and friendly.
    """,
    tools=[google_search,
        PreloadMemoryTool(),
        load_memory_tool,
        AgentTool(todo_agent),
        AgentTool(txn_agent),
    ],
    after_agent_callback=auto_save_session_to_memory_callback,
)
