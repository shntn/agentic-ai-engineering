"""再帰的な分割とオーバーラップによるテキストのチャンク分割。"""

from dataclasses import dataclass, field


@dataclass
class Chunk:
    """出典メタデータを持つテキストのチャンク。"""

    content: str
    source: str
    chunk_index: int
    start_char: int
    end_char: int
    metadata: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        """このチャンクの一意な識別子。"""
        return f"{self.source}:{self.chunk_index}"


# 順番に試すセパレーター——最も意味のある境界から先に分割を試みる
DEFAULT_SEPARATORS = ["\n\n", "\n", "。", " "]


def recursive_split(
    text: str,
    source: str,
    chunk_size: int = 512,
    chunk_overlap: int = 64,
    separators: list[str] | None = None,
) -> list[Chunk]:
    """自然な境界で再帰的にテキストを分割し、オーバーラップを付与する。

    セパレーターを順番に試す: 二重改行、単一改行、文末、スペース。
    最終手段として文字単位での分割にフォールバックする。
    """
    if not text.strip():
        return []

    seps = separators or DEFAULT_SEPARATORS
    raw_chunks = _split_recursive(text, chunk_size, seps)

    # 連続するチャンク間にオーバーラップを追加する
    chunks = []
    for i, raw in enumerate(raw_chunks):
        # 前のチャンクの末尾をオーバーラップとして先頭に追加する
        if i > 0 and chunk_overlap > 0:
            prev = raw_chunks[i - 1]
            overlap_text = prev[-chunk_overlap:]
            raw = overlap_text + raw

        start_char = (
            text.find(raw_chunks[i][:50]) if i == 0 else max(0, text.find(raw_chunks[i][:50]))
        )
        chunks.append(
            Chunk(
                content=raw.strip(),
                source=source,
                chunk_index=i,
                start_char=start_char,
                end_char=start_char + len(raw_chunks[i]),
            )
        )

    return [c for c in chunks if c.content]


def _split_recursive(text: str, chunk_size: int, separators: list[str]) -> list[str]:
    """段階的に細かいセパレーターを使ってテキストを再帰的に分割する。"""
    if len(text) <= chunk_size:
        return [text]

    # 各セパレーターを順番に試す
    for sep in separators:
        if sep in text:
            parts = text.split(sep)
            result = []
            current = ""

            for part in parts:
                candidate = current + sep + part if current else part
                if len(candidate) <= chunk_size:
                    current = candidate
                else:
                    if current:
                        result.append(current)
                    # 1つのパートがchunk_sizeを超える場合、より細かいセパレーターで分割する
                    if len(part) > chunk_size:
                        remaining_seps = separators[separators.index(sep) + 1 :]
                        if remaining_seps:
                            result.extend(_split_recursive(part, chunk_size, remaining_seps))
                        else:
                            # 最終手段: 文字数で強制的に分割する
                            for j in range(0, len(part), chunk_size):
                                result.append(part[j : j + chunk_size])
                    else:
                        current = part

            if current:
                result.append(current)

            return result

    # セパレーターが見つからない——強制的に分割する
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
