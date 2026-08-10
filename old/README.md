<div align="center">

# 🤖 J.A.R.V.I.S.

**An extensible framework for building, orchestrating, and running autonomous AI agents.**

</div>

<p align="center">
  <a href="https://python.org"><img src="https://img.shields.io/badge/Python-≥3.12-blue.svg" alt="Python Version"></a>
  <a href="/Volumes/data/workspace/Javis/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License"></a>
  <a href="https://github.com/psf/black"><img src="https://img.shields.io/badge/code%20style-black-000000.svg" alt="Code Style"></a>
</p>

<p align="center">
  <a href="#-key-features">Key Features</a> •
  <a href="#-getting-started">Getting Started</a> •
  <a href="#-usage">Usage</a> •
  <a href="#-architecture-overview">Architecture</a> •
  <a href="#-contributing">Contributing</a>
</p>

---

**J.A.R.V.I.S.** is an advanced AI framework designed to automate complex tasks. By combining specialized AI agents (like a Researcher, Planner, and Coordinator) with a dynamic workflow engine, J.A.R.V.I.S. can autonomously execute multi-step tasks ranging from in-depth research to content creation.

Its modular and extensible design makes it an ideal platform for developers and researchers looking to harness the power of Large Language Models (LLMs) for sophisticated applications.

*This project serves as an exercise in building a deep search agent. The core workflow and prompts are heavily inspired by, and a tribute to, the original [deerflow](https://github.com/bytedance/deer-flow) project.*

## ✨ Key Features

- **🤖 Agentic Architecture**: Utilizes a team of specialized AI agents (e.g., `Researcher`, `Planner`, `Coordinator`) that collaborate to solve complex problems.
- **🚀 Dynamic Workflow Engine**: Supports conditional logic, parallel execution, and asynchronous operations for flexible and powerful task orchestration.
- **🧩 Modular & Extensible**: Built with a clean, decoupled architecture that makes it easy to add new agents, tools, or workflows.
- **🏗️ Built on Agno**: Leverages [Agno v2.0.4](https://github.com/agno-agi/agno) as the core framework for agent development, providing a solid foundation for building and managing agents.
- **⚙️ Configuration-Driven**: Easily manage LLM providers, API keys, and agent behaviors through a simple `conf.yaml` file.
- **🌐 Async First**: Natively built with `asyncio` for high-performance I/O operations, perfect for tasks like web crawling and tool usage.
- **🖥️ Multiple Interaction Modes**: Offers a Command-Line Interface (CLI), an interactive session mode, and an API server to fit various use cases.

## 🚀 Getting Started

### 1. Prerequisites

- [Python 3.12+](https://www.python.org/)
- [Git](https://git-scm.com/)
- [uv](https://astral.sh/uv) (Recommended for environment management)

### 2. Installation

First, clone the repository to your local machine:

```bash
git clone https://github.com/rexrens/javis.git
cd javis
```

Next, we recommend using `uv` to create a virtual environment and install dependencies, as it offers superior performance.

```bash
# Create the virtual environment and sync dependencies using uv
uv venv
source .venv/bin/activate
uv sync
```

### 3. Configuration

Before you begin, you need to configure your LLM API keys. Copy the example configuration file:

```bash
cp conf.yaml.example conf.yaml
```

Then, edit `conf.yaml` and fill in your API key and other desired settings:

```yaml
llm:
  provider: "openai" # or "google", "anthropic"
  api_key: "sk-..."
  model: "gpt-4-turbo"
```

## 🎯 Usage

The main entry point for running the agent workflow is `main.py`. The script is currently configured to demonstrate the deep search process by selecting a random topic from a predefined list and running the full agentic workflow.

To run the demonstration, simply execute the file:

```bash
python main.py
```

You will see the entire process streamed to your console, including the planning steps, the parallel research tasks, and the final synthesized report.

### Testing Your Own Topics

To run the workflow with your own topic, you can directly modify `main.py`:

```python
# In main.py

# ...

# Test with your own topic instead of a random one
topic = "The future of decentralized finance"

workflow.print_response(
    input=topic,
    stream=True,
    stream_intermediate_steps=True,
    show_step_details=True
)
```

## 🏛️ Architecture Overview

The core architecture of J.A.R.V.I.S. is composed of several key components:

- **`main.py` / `server.py`**: The entry points for the CLI and the API server.
- **`src/workflow.py`**: The workflow engine that manages and executes the task graph.
- **`src/agents.py`**: Defines the specialized AI agents, each with a unique role and capabilities.
- **`src/llm.py`**: The interface for interacting with the underlying Large Language Models (LLMs), abstracting away provider-specific details.
- **`src/prompts/`**: A directory containing the system prompts that define the behavior and expertise of each agent.
- **`src/config.py`**: Responsible for loading and validating the `conf.yaml` configuration.

This layered and decoupled design makes J.A.R.V.I.S. both powerful and easy to maintain.

## 🔬 Workflow Analysis

The core of J.A.R.V.I.S. is its multi-agent workflow designed for handling complex research queries. The process begins with a user query and flows through a series of specialized agents to generate a comprehensive answer.

The following diagram illustrates the typical "deep search" workflow:


``` mermaid
graph TD
    A[User Query] --> B{Coordinator};

    subgraph "Phase 1: Planning"
        B --> C[Planner Agent];
        C --> D[1. Generate Research Plan];
    end

    subgraph "Phase 2: Execution"
        D --> B;
        B -- For each research step --> F[Researcher Agent];
        F --> G[2. Execute Research (e.g., Web Search)];
    end

    subgraph "Phase 3: Synthesis"
        G --> B;
        B --> I[3. Synthesize Final Report];
        I --> J[Formatted Answer];
    end
```


1.  **Planning**: The `Planner` agent receives the user's query from the `Coordinator` and breaks it down into a structured plan with discrete research steps.
2.  **Execution**: The `Coordinator` iterates through the plan, dispatching each research step to one or more `Researcher` agents. These agents run in parallel to gather the necessary information.
3.  **Synthesis**: Once all research is complete, the `Coordinator` gathers the findings and synthesizes them into a single, coherent report, which is then presented to the user.

## 🤝 Contributing

Contributions are welcome! If you'd like to contribute to J.A.R.V.I.S., please follow these steps:

1.  Fork the repository.
2.  Create your feature branch (`git checkout -b feature/AmazingFeature`).
3.  Commit your changes (`git commit -m 'Add some AmazingFeature'`).
4.  Push to the branch (`git push origin feature/AmazingFeature`).
5.  Open a Pull Request.

Please make sure to run tests before submitting your pull request.

## 📝 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---
<div align="center">
Made with ❤️ by the J.A.R.V.I.S. Team
</div>
