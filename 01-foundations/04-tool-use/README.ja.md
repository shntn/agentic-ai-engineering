<!-- ---
title: "ツール使用"
description: "LLMが関数を呼び出し、外部システムとやり取りできるようにする"
icon: "wrench"
--- -->

# ツール使用

LLMに関数（ツール）を呼び出して現実世界とやり取りする能力を与える方法を学びます。このチュートリアルでは、ツールを定義し、ツール呼び出しを処理し、モデルに代わって関数を実行する方法を実演します。

## 🎯 学べること

- LLMが理解できるようJSON Schemaでツールを定義する
- ツール呼び出しループ（リクエスト → 実行 → 応答）を処理する
- ガードレールを備えて安全に関数を実行する
- 単一の応答内で複数のツール呼び出しを扱う

## 📦 利用可能なサンプル

| プロバイダー                                        | ファイル                                                 | 説明                        |
| ----------------------------------------------- | ---------------------------------------------------- | ---------------------------------- |
| ![Anthropic](../../.docs/badges/anthropic.svg) | [01_tool_use_anthropic.py](01_tool_use_anthropic.py) | Claude Messages API を使ったツール使用  |
| ![OpenAI](../../.docs/badges/openai.svg)       | [02_tool_use_openai.py](02_tool_use_openai.py)       | OpenAI Responses API を使ったツール使用 |

## 🚀 クイックスタート

> **前提条件:** Python 3.11+、APIキー、uv。セットアップの詳細は [SETUP.md](../../SETUP.md) を参照してください。

```bash
uv run --directory 01-foundations/04-tool-use python {script_name}

# 例
uv run --directory 01-foundations/04-tool-use python 01_tool_use_anthropic.py
```

または、[Code Runner](https://marketplace.visualstudio.com/items?itemName=formulahendry.code-runner) VS Code拡張機能を使えば、開いているスクリプトをワンクリックで実行できます。

## 🔑 キーコンセプト

### 1. ツールの定義

ツールはJSON Schemaを使って定義され、LLMがどの関数が利用可能かを理解できるようにします:

**Anthropic:**
```python
TOOLS = [
    {
        "name": "calculator",
        "description": "Performs basic arithmetic operations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["add", "subtract", "multiply", "divide"],
                },
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["operation", "a", "b"],
        },
    },
]
```

**OpenAI:**
```python
TOOLS = [
    {
        "type": "function",
        "name": "calculator",
        "description": "Performs basic arithmetic operations.",
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["add", "subtract", "multiply", "divide"],
                },
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["operation", "a", "b"],
        },
    },
]
```

### 2. ツール呼び出しループ

LLMはツールを直接実行しません — LLMはツール呼び出しを要求し、それをあなたが実行します:

```
ユーザーメッセージ
    |
LLMの応答（tool_use を含む）
    |
ツールを実行 -> 結果を取得
    |
結果をLLMに送り返す
    |
LLMの応答（最終回答）
```

**Anthropic:**
```python
response = client.messages.create(
    model="claude-sonnet-4-6",
    tools=TOOLS,
    messages=messages,
)

if response.stop_reason == "tool_use":
    for block in response.content:
        if isinstance(block, ToolUseBlock):
            result = execute_tool(block.name, block.input)
            # tool_use_id とともに結果を送り返す
```

**OpenAI:**
```python
response = client.responses.create(
    model="gpt-4.1",
    tools=TOOLS,
    input=messages,
)

for output in response.output:
    if output.type == "function_call":
        result = execute_tool(output.name, json.loads(output.arguments))
        # call_id とともに結果を送り返す
```

### 3. ガードレールを備えたツールの実装

特にシステムレベルのツールでは、ツールの入力を必ず検証・サニタイズしてください:

```python
BLOCKED_COMMANDS = ["rm", "sudo", "chmod", "shutdown", ">", ">>"]

def run_bash(command: str, timeout: int = 30) -> dict:
    """安全ガードレールを備えて bash コマンドを実行する。"""
    # 危険なコマンドをブロック
    for blocked in BLOCKED_COMMANDS:
        if blocked in command.lower():
            return {"error": f"Command blocked: contains '{blocked}'"}

    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        timeout=timeout,
    )
    return {"stdout": result.stdout, "stderr": result.stderr}
```

### 4. 複数のツール呼び出しの処理

LLMは単一の応答内で複数のツール呼び出しを要求することがあります。続行する前に、そのすべてを処理してください:

**Anthropic:**
```python
tool_results = []
for tool_use in tool_uses:
    result = execute_tool(tool_use.name, tool_use.input)
    tool_results.append({
        "type": "tool_result",
        "tool_use_id": tool_use.id,
        "content": json.dumps(result),
    })
messages.append({"role": "user", "content": tool_results})
```

**OpenAI:**
```python
# まず関数呼び出しをメッセージに追加
messages.extend(response.output)

# 続けて結果を追加
for func_call in function_calls:
    result = execute_tool(func_call.name, json.loads(func_call.arguments))
    messages.append({
        "type": "function_call_output",
        "call_id": func_call.call_id,
        "output": json.dumps(result),
    })
```

## 🧰 このチュートリアルのツール

| ツール         | 説明                                        |
| ------------ | -------------------------------------------------- |
| `calculator` | 基本的な算術演算（加算、減算、乗算、除算） |
| `read_file`  | ファイルシステムからファイルの内容を読み込む             |
| `run_bash`   | シェルコマンドを実行する（安全ガードレール付き）    |

## 🏗️ コード構造

両方のサンプルは一貫した構造に従っています:

```python
# 1. ツールをJSON Schemaとして定義
TOOLS = [...]

# 2. ツール関数を実装
def calculator(operation: str, a: float, b: float) -> dict:
    ...

def read_file(path: str) -> dict:
    ...

def run_bash(command: str) -> dict:
    ...

# 3. ツール実行ディスパッチャー
TOOL_FUNCTIONS = {"calculator": calculator, "read_file": read_file, ...}

def execute_tool(name: str, input: dict) -> Any:
    return TOOL_FUNCTIONS[name](**input)


# 4. ツールループを備えたチャットクラス
class ToolUseChat:
    def send_message(self, message: str) -> str:
        while True:
            response = self.client.create(tools=TOOLS, ...)

            if has_tool_calls(response):
                execute_tools_and_add_results()
                continue
            else:
                return response.text


# 5. メインのオーケストレーション
def main():
    chat = ToolUseChat(model, token_tracker, console)
    while True:
        user_input = input()
        response = chat.send_message(user_input)
        print(response)
```

## 👉 次のステップ

ツール使用を習得したら、次に進みましょう:
- **[Agent Loop](../05-agent-loop/README.md)** - タスクを完了するためにツールを使用する自律エージェントを構築する
- **実験する** - Web検索、データベースクエリ、API呼び出しなど、さらにツールを追加してみる
- **探求する** - ツール選択モード（`auto`、`required`、`none`）を実装してみる
