<!-- ---
title: "シンプルなLLM呼び出し"
description: "Anthropic Claude、OpenAI GPT、LiteLLMを使って最初のAPI呼び出しを行う"
icon: "zap"
--- -->

# シンプルなLLM呼び出し

LLM APIへの基本的な呼び出し方法を学びます。このチュートリアルでは、さまざまなLLMプロバイダーとやり取りし、シンプルなテキスト応答を取得する方法を実演します。

## 🎯 学べること

- さまざまなプロバイダー向けにLLMクライアントを初期化・設定する
- 単一のプロンプトでシンプルなAPI呼び出しを行う
- API呼び出しからテキスト応答を抽出する
- 統一インターフェース（LiteLLM）を使って複数のプロバイダーを扱う

## 📦 利用可能なサンプル

| プロバイダー                                        | ファイル                                                 | 説明                        |
| ----------------------------------------------- | ---------------------------------------------------- | ---------------------------------- |
| ![Anthropic](../../.docs/badges/anthropic.svg) | [01_llm_call_anthropic.py](01_llm_call_anthropic.py) | 基本的な Claude Messages API 呼び出し    |
| ![OpenAI](../../.docs/badges/openai.svg)       | [02_llm_call_openai.py](02_llm_call_openai.py)       | 基本的な OpenAI Responses API 呼び出し   |
| ![LiteLLM](../../.docs/badges/litellm.svg)     | [03_llm_call_litellm.py](03_llm_call_litellm.py)     | 任意のプロバイダーに対応する統一インターフェース |

## 🚀 クイックスタート

> **前提条件:** Python 3.11+、APIキー、uv。セットアップの詳細は [SETUP.md](../../SETUP.md) を参照してください。

```bash
uv run --directory 01-foundations/01-simple-llm-call python {script_name}

# 例
uv run --directory 01-foundations/01-simple-llm-call python 01_llm_call_anthropic.py
```

または、[Code Runner](https://marketplace.visualstudio.com/items?itemName=formulahendry.code-runner) VS Code拡張機能を使えば、開いているスクリプトをワンクリックで実行できます。

## 🔑 キーコンセプト

### 1. シンプルなLLM呼び出しフロー

```mermaid
---
config:
  look: handDrawn
  theme: neutral
---
flowchart LR
    A(["⚡ 入力プロンプト"]) -->|request| B["🧠 LLM 呼び出し   "]
    B -->|response| C(["📄 応答テキスト"])
```

### 2. LLMクライアントの初期化

各プロバイダーはそれぞれ独自のクライアント初期化方法を持っています:

**Anthropic:**
```python
import anthropic

client = anthropic.Anthropic()  # 環境変数の ANTHROPIC_API_KEY を使用
model = "claude-sonnet-4-6"
```

**OpenAI:**
```python
from openai import OpenAI

client = OpenAI()  # 環境変数の OPENAI_API_KEY を使用
model = "gpt-4.1"
```

**LiteLLM:**
```python
from litellm import completion

# クライアントは不要 — completion() を呼び出すだけ
# モデル名に応じて適切なAPIキーが使用される
```

### 3. API呼び出しを行う

**Anthropic（Messages API）:**
```python
response = client.messages.create(
    model="claude-sonnet-4-6",
    temperature=0.1,
    max_tokens=1024,
    system="You are a helpful AI assistant.",
    messages=[
        {"role": "user", "content": "Hello!"}
    ],
)
text = response.content[0].text
```

**OpenAI（Responses API）:**
```python
response = client.responses.create(
    model="gpt-4.1",
    temperature=0.1,
    max_output_tokens=1024,
    instructions="You are a helpful AI assistant.",
    input="Hello!",
)
text = response.output_text
```

**LiteLLM（統一API）:**
```python
response = completion(
    model="gpt-4.1",  # または "claude-sonnet-4-6"
    temperature=0.1,
    max_tokens=1024,
    messages=[
        {"role": "system", "content": "You are a helpful AI assistant."},
        {"role": "user", "content": "Hello!"}
    ],
)
text = response.choices[0].message.content
```

> これらの例は基本的な（ストリーミングなしの）API呼び出しを示しています。ストリーミング応答については、[Anthropic Streaming docs](https://docs.anthropic.com/en/api/messages-streaming)、[OpenAI Streaming docs](https://platform.openai.com/docs/api-reference/streaming)、[LiteLLM Streaming docs](https://docs.litellm.ai/docs/completion/stream) を参照してください。

### 4. 主要な設定パラメータ

**Model**: 使用するLLMを指定します（例: `claude-sonnet-4-6`、`gpt-4.1`）

**Temperature**: ランダム性を制御します（0.0 = 決定的、1.0 = 創造的）
- 低い値（0.0〜0.3）: 事実に基づいた一貫性のある応答向け
- 高い値（0.7〜1.0）: 創造的で多様な出力向け

**Max Tokens**: 応答の長さを制限します
- Anthropic/LiteLLM: `max_tokens`
- OpenAI Responses API: `max_output_tokens`

**System Prompt**: アシスタントの振る舞いとコンテキストを定義します
- Anthropic: `system` パラメータ
- OpenAI: `instructions` パラメータ
- LiteLLM: `messages` 配列内のシステムメッセージ

> `top_p`、`top_k`、`stop_sequences`、`presence_penalty`、`frequency_penalty`、`seed` などのその他の高度なパラメータについては、今後のチュートリアルで扱います。

## 🏗️ コード構造

すべてのサンプルは一貫した構造に従っています:

```python
class LLMClient:
    """LLMとのやり取りロジックをカプセル化する。"""

    def __init__(self, model: str):
        self.client = ...  # APIクライアントを初期化
        self.model = model
        self.system_prompt = "..."

    def run(self, prompt: str) -> str:
        """単一のLLM呼び出しを実行する。"""
        # 1. API呼び出しを行う
        response = self.client...

        # 2. テキストを抽出して返す
        return response_text


def main() -> None:
    """実行フローを調整する。"""
    # 1. クライアントを初期化
    client = LLMClient("model-name")

    # 2. プロンプトを定義
    prompt = "..."

    # 3. 応答を取得
    response = client.run(prompt)

    # 4. 結果を表示
    logger.info(f"Response: {response}")
```

## 👉 次のステップ

シンプルなLLM呼び出しを習得したら、次に進みましょう:
- **[プロンプトエンジニアリング](../02-prompt-engineering/README.md)** - より良い応答を得るための効果的なプロンプトの作り方を学ぶ
- **実験する** - 異なるモデル、temperature、プロンプトを試してみる
- **探求する** - リトライロジックやエラーハンドリングなどの機能を追加してサンプルを改造してみる
