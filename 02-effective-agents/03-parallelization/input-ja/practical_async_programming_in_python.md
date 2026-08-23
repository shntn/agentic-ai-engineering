# Pythonにおける非同期プログラミングの習得: 基礎から本番運用まで

## I/Oバウンドなタスクで、スレッドやマルチプロセスよりasync/awaitを選ぶべきタイミング

判断はシンプルです: asyncが真価を発揮するのはI/Oバウンドな処理だけです。可能な限りasyncioを使い、どうしても必要な場合のみスレッドやconcurrent.futuresを使う——これを判断の目安にしてください。

I/Oバウンドなタスクでは、特に大量の並行タスクを扱う場合、非同期I/Oはマルチスレッドよりも高いパフォーマンスを発揮することがよくあります。これはスレッド管理のオーバーヘッドを回避できるためです。Linux上ではOSスレッド1つあたりデフォルトで8MBのメモリを消費します。つまり1,000スレッドではスタック領域だけで8GBのメモリを消費することになります。一方asyncioは、最小限のメモリオーバーヘッドで単一スレッド内に100,000個のコルーチンを実行でき、各コルーチンはわずか約4KBしか使用しません。

決定的な利点は協調的並行性です。コルーチンは`await`に到達すると自発的にイベントループへ制御を譲り渡し、その間に他の何千ものコルーチンが処理を進められるようになります。

CPUバウンドな処理には別のツールが必要です。PythonのGlobal Interpreter Lock（GIL）はスレッドによる真の並列処理を妨げるため、計算負荷の高いタスクに対してはasyncもスレッドも同様に効果がありません。重い計算処理を行うなら`multiprocessing`を使いましょう。それ以外のI/O待ちが発生する処理には、asyncが最適です。

## asyncioのイベントループとコルーチンで最初の非同期アプリケーションを構築する

Pythonにおける非同期I/Oの中核となる構成要素は、awaitable（待機可能）なオブジェクト——多くの場合はコルーチン——であり、イベントループがこれをスケジュールし非同期に実行します。このプログラミングモデルにより、単一の実行スレッド内で複数のI/Oバウンドなタスクを効率的に管理できます。

まずはシンプルな例から始めましょう:

```python
import asyncio

async def fetch_data(name):
    print(f"Starting {name}")
    await asyncio.sleep(1)  # I/Oをシミュレート
    print(f"Finished {name}")
    return f"Data from {name}"

async def main():
    # 3つのコルーチンを並行実行する
    results = await asyncio.gather(
        fetch_data("API-1"),
        fetch_data("API-2"),
        fetch_data("API-3")
    )
    print(results)

asyncio.run(main())
```

これは合計で約1秒しかかかりません。3秒ではありません。あるTaskがイベントループ内で実行されている間、同じスレッド内で他のTaskは実行できません。Taskがawait式を実行すると、実行中のTaskは一時停止し、イベントループは次のTaskを実行します。

イベントループはシングルスレッドのスケジューラーとして動作します。実行可能なタスクのキューと、I/Oイベントのレジストリを保持しています。コードが`await`に達すると、イベントループはそのコルーチンを一時停止し、次に実行可能なタスクを実行します——これによりスレッドのオーバーヘッドなしで並行性を実現しています。

メインのコルーチンを開始する際は常に`asyncio.run()`を使用してください。この関数はイベントループを自動的に作成し、コルーチンを実行し、終了時にリソースを適切にクリーンアップします。

## ブロッキングなしで並行API呼び出しとデータベース操作を処理する

実際の非同期処理には適切なライブラリが必要です。非同期I/Oコードを書くための一般的な2つのツールとして、HTTP呼び出し用のaiohttpライブラリと、データベースアクセス用のSQLAlchemyの非同期ORMがあります。

`aiohttp`を使った並行API呼び出しのパターンは次の通りです:

```python
import aiohttp
import asyncio

async def fetch_url(session, url):
    async with session.get(url) as response:
        return await response.json()

async def fetch_all_urls(urls):
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_url(session, url) for url in urls]
        return await asyncio.gather(*tasks)

urls = [
    "https://api.example.com/user/1",
    "https://api.example.com/user/2",
    "https://api.example.com/user/3"
]

asyncio.run(fetch_all_urls(urls))
```

単一の`ClientSession`を再利用することで、複数のリクエストで同じTCPコネクションを再利用するHTTPコネクションプーリングが可能になります。これにより、新規接続を都度作成する場合と比べてリクエストあたり20〜50ミリ秒のレイテンシ削減が見込めます。リクエストごとに新しいセッションを作成することは絶対に避けてください——それでは意味がありません。

データベースには`asyncpg`（PostgreSQL）や`aiosqlite`のような非同期ドライバを使用してください。`sqlite3`や`psycopg2`のような標準的なデータベースライブラリは、クエリ実行中にイベントループをブロックしてしまいます。例えば`asyncpg`は10,000件以上の同時データベース接続を処理できますが、同期的な`psycopg2`では10,000個のスレッドが必要になります。

## 非同期コードのデバッグ: async関数内でのブロッキング呼び出しなどよくある落とし穴とその解決策

最も厄介なバグは、非同期コード内でブロッキング関数を呼び出すことです。`time.sleep()`を使うとイベントループ全体が凍結し、並行実行中のすべてのコルーチンがブロックされます:

```python
async def bad_example():
    import time
    time.sleep(1)  # すべてをブロックしてしまう

async def good_example():
    await asyncio.sleep(1)  # イベントループに制御を譲る
```

async関数内でCPUバウンドな処理を行う場合は、`loop.run_in_executor()`を使ってブロッキング処理をスレッドプール内で実行してください:

```python
async def compute_heavy():
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, expensive_calculation)
    return result
```

もう一つよくある間違いは`await`の付け忘れです。awaitがないと、戻り値の代わりにコルーチンオブジェクトが返ってきます。Python 3.7以降では`RuntimeWarning: coroutine 'function_name' was never awaited`という警告が出ます。対処法は常に同じです——`await`を追加してください。

最大の間違いは、CPU負荷の高い処理をコルーチン内で直接実行することです。100ミリ秒かかる計算処理は、その間ずっと10,000個の並行コルーチンを凍結させてしまいます。良い習慣として、CPUバウンドな処理はスレッドプールで実行しましょう。

## コネクションプーリング、レート制限、適切なエラーハンドリングで非同期アプリケーションをスケールする

本番環境の非同期処理にはガードレールが必要です。コネクションプーリングはセッションの再利用によって自動的に得られますが、レート制限には明示的な制御が必要です。コルーチンは軽量（各約4KB）ですが、50,000件以上の並行タスクを作成すると、ネットワーク接続が飽和し、対象サーバーに過負荷をかける可能性があります。

`asyncio.Semaphore`を使って並行処理数を制限します:

```python
async def rate_limited_fetch(semaphore, session, url):
    async with semaphore:
        async with session.get(url) as response:
            return await response.json()

async def fetch_with_limits(urls, max_concurrent=5):
    semaphore = asyncio.Semaphore(max_concurrent)
    async with aiohttp.ClientSession() as session:
        tasks = [
            rate_limited_fetch(semaphore, session, url)
            for url in urls
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)
```

`return_exceptions=True`パラメータは非常に重要です。デフォルトでは、`asyncio.gather()`は1つのタスクが失敗すると残りのすべてのタスクをキャンセルします。`return_exceptions=True`を指定すると、失敗した処理は例外を返し、成功した処理は結果を返すようになります:

```python
results = await asyncio.gather(*tasks, return_exceptions=True)
for i, result in enumerate(results):
    if isinstance(result, Exception):
        logger.error(f"URL {i} failed: {result}")
    else:
        process(result)
```

数千件の同時接続を扱う本番システムでは、プーリングのためのセッション再利用、レート制限のための`Semaphore`（通常はサービスあたり同時実行数10〜100）、耐障害性のための`gather(..., return_exceptions=True)`、そして永続化処理でのブロッキングを避けるための非同期データベースライブラリを組み合わせましょう。Instagramのエンジニアリングチームは、これらのパターンを用いてPythonプロセスあたり10,000件以上の同時接続を処理していると報告しています。

## まとめ

• **エントリーポイントとして`asyncio.run()`を使用し、コルーチン呼び出しには常に`await`を付けてください**——awaitの付け忘れはランタイム警告を発生させ、値の代わりにコルーチンオブジェクトを返してしまいます。

• **複数のリクエスト間で`aiohttp.ClientSession`オブジェクトを再利用してください**。コネクションプーリングが有効になり、呼び出しごとのレイテンシを20〜50ミリ秒削減できます。

• **CPUバウンドな処理は`loop.run_in_executor(None, function)`でラップしてください**。イベントループのブロッキングを防げます——`time.sleep()`や重い計算処理をasync関数内で直接呼び出してはいけません。

• **`asyncio.Semaphore(N)`でレート制限を実装してください**。Nは通常、外部サービスあたり同時実行数10〜100とし、対象への過負荷を避けます。

• **`asyncio.gather()`では常に`return_exceptions=True`を使用してください**。1つのタスクの失敗によって、バッチ内の残りすべての処理がキャンセルされるのを防げます。

## 参考文献

- [Faster Python: Concurrency in async/await and threading | The PyCharm Blog](https://blog.jetbrains.com/pycharm/2025/06/concurrency-in-async-await-and-threading/)
- [Asyncio Vs Threading In Python - GeeksforGeeks](https://www.geeksforgeeks.org/python/asyncio-vs-threading-in-python/)
- [Speed Up Your Python Program With Concurrency – Real Python](https://realpython.com/python-concurrency/)
- [Is asyncio python better than threading? | ProxiesAPI](https://proxiesapi.com/articles/is-asyncio-python-better-than-threading)
- [Asynchronous programming vs Threading in Python | by Sanjeet Shukla | Medium](https://medium.com/@sanjeets1900/asynchronous-programming-vs-threading-in-python-d59306a853a7)
- [Multiprocessing VS Threading VS AsyncIO in Python - Lei Mao's Log Book](https://leimao.github.io/blog/Python-Concurrency-High-Level/)
- [Choosing between free threading and async in Python - Optiver](https://optiver.com/working-at-optiver/career-hub/choosing-between-free-threading-and-async-in-python/)
- [What are the advantages of asyncio over threads? - Ideas - Discussions on Python.org](https://discuss.python.org/t/what-are-the-advantages-of-asyncio-over-threads/2112)
- [Python's asyncio: A Hands-On Walkthrough – Real Python](https://realpython.com/async-io-python/)
- [Why Should Async Get All The Love?: Advanced Control Flow With Threads](https://emptysqua.re/blog/why-should-async-get-all-the-love/)
- [Python's asyncio: A Hands-On Walkthrough – Real Python](https://realpython.com/async-io-python/)
- [Event Loop — Python 3.14.3 documentation](https://docs.python.org/3/library/asyncio-eventloop.html)
- [A Conceptual Overview of asyncio — Python 3.14.3 documentation](https://docs.python.org/3/howto/a-conceptual-overview-of-asyncio.html)
- [Asyncio Event Loops Tutorial | TutorialEdge.net](https://tutorialedge.net/python/concurrency/asyncio-event-loops-tutorial/)
- [Understanding Python’s asyncio: A Deep Dive into the Event Loop | by Hyunil Kim | 딜리버스 | Medium](https://medium.com/delivus/understanding-pythons-asyncio-a-deep-dive-into-the-event-loop-89a6c5acbc84)
- [Coroutines and Tasks — Python 3.14.3 documentation](https://docs.python.org/3/library/asyncio-task.html)
- [Developing with asyncio — Python 3.14.3 documentation](https://docs.python.org/3/library/asyncio-dev.html)
- [Python/Django AsyncIO Tutorial: Async Programming in Python](https://djangostars.com/blog/asynchronous-programming-in-python-asyncio/)
- [Mastering Python’s Asyncio: A Practical Guide | by Moraneus | Medium](https://medium.com/@moraneus/mastering-pythons-asyncio-a-practical-guide-0a673265cf04)
- [Python Async Programming: The Complete Guide | DataCamp](https://www.datacamp.com/tutorial/python-async-programming)
- [Async Concurrency in Python: comparing aiohttp.ClientSession and SQLAlchemy AsyncSession under asyncio.gather | by Lynn G. Kwong | Level Up Coding](https://levelup.gitconnected.com/async-concurrency-in-python-comparing-aiohttp-clientsession-033c234a4572)
- [A Practical Guide to Concurrent Requests with AIOHTTP in Python](https://apidog.com/blog/aiohttp-concurrent-request/)
- [Speeding up ETLHelper’s API transfers with asyncio - British Geological Survey](https://britishgeologicalsurvey.github.io/open-source/async-etlhelper-api-transfer/)
- [Python Asynchronous Programming — asyncio and aiohttp | by Aditya Kolpe | Medium](https://medium.com/@adityakolpe/python-asynchronous-programming-with-asyncio-and-aiohttp-186378526b01)
- [Making Concurrent HTTP requests with Python AsyncIO | LAAC Technology](https://www.laac.dev/blog/concurrent-http-requests-python-asyncio/)
- [Handling Large Requests with aiohttp and asyncio in Python | by Rahul Patel | Medium](https://medium.com/@rspatel031/handling-large-requests-with-aiohttp-and-asyncio-in-python-d603b2de5c69)
- [Making Parallel HTTP Requests With aiohttp (Video) – Real Python](https://realpython.com/lessons/making-parallel-http-requests-aiohttp/)
- [Making Concurrent Requests with aiohttp in Python | ProxiesAPI](https://proxiesapi.com/articles/making-concurrent-requests-with-aiohttp-in-python)
- [Concurrent HTTP Requests with Python3 and asyncio · GitHub](https://gist.github.com/debugtalk/3d26581686b63c28227777569c02cf2c)
- [asyncio — Asynchronous I/O](https://docs.python.org/3/library/asyncio.html)
