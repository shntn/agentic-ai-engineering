# ブラウザを超えたWebAssembly: サーバーサイド・エッジコンピューティングの可能性を解き放つ

WebAssemblyはブラウザのサンドボックスを抜け出し、本番運用可能なサーバーサイド技術として確立されました。Shopifyは信頼できないユーザースクリプトの実行にWASMを使い、Fastlyは数百万件ものエッジリクエストをWASMモジュール経由で処理し、Dockerは今やLinuxバイナリの代わりに純粋なWebAssemblyを実行するコンテナを提供しています。

## wasmtimeとwasmerランタイムでNode.jsのWebAssemblyモジュールを実行する

Node.jsのWebAssembly分野では、2つのランタイムが主流であり、それぞれ異なるパフォーマンス特性を持っています:

**Wasmtime**はネイティブ実行速度の85〜90%を、メモリオーバーヘッド25MBで実現し、WASI準拠が求められるサーバーサイドアプリケーションに最適です。Bytecode AllianceがWASIのリファレンス実装として保守しています。

**Wasmer**はメモリ使用量わずか18MBで、ネイティブパフォーマンスの80〜85%を達成します。CLIツールやプラグインシステムへの軽量な組み込みに最適化されています。

このパフォーマンスの差はスケール時に重要になります。Shopifyの内部ベンチマークでは、Wasmtimeは10,000件の同時スクリプト実行をより低いメモリ負荷で処理できる一方、Wasmerは高速なモジュールインスタンス化が求められるシナリオで優れています。

Node.jsでCPU負荷の高いWASMを実行する方法は次の通りです:

```javascript
const { WASI } = require('wasi');
const fs = require('fs');
const wasmBuffer = fs.readFileSync('fibonacci.wasm');

const wasi = new WASI({
  version: 'preview1',
  args: process.argv,
  env: process.env,
  preopens: { '/local': '/tmp' }
});

const instance = new WebAssembly.Instance(
  new WebAssembly.Module(wasmBuffer),
  wasi.getImportObject()
);

// WASM関数を実行する
const fib = instance.exports.fibonacci;
console.log(fib(40)); // ネイティブ速度の約85%で動作
```

このパターンでは、Node.jsがI/O・ネットワーキング・システム統合を担当する一方、計算負荷の高い処理を分離できます。

## AWS LambdaとCloudflare WorkersでWebAssemblyを使ったサーバーレス関数を構築する

パフォーマンスベンチマークからは、サーバーレスWASMプラットフォーム間に大きな差があることが分かります:

**Cloudflare Workers**はV8アイソレートを使ってコールドスタートを5ミリ秒未満に抑え、95パーセンタイルでLambdaより441%高速なパフォーマンスを実現します。Workersは世界200都市以上で稼働しており、レイテンシに敏感なアプリケーションに最適です。

**AWS Lambda**とカスタムWASMランタイムの組み合わせは、S3・DynamoDB・API Gatewayとシームレスに統合できますが、従来型コンテナのコールドスタートのペナルティ（100〜500ミリ秒）を抱えています。

Cloudflare WorkersはRustからの直接コンパイルに対応しています:

```rust
use worker::*;

#[event(fetch)]
pub async fn main(req: Request, _env: Env, _ctx: Context) -> Result<Response> {
    let image_data = req.bytes().await?;
    let compressed = compress_image(&image_data)?; // CPU負荷の高いWASM処理
    Response::ok(compressed)
}
```

AWS Lambdaの場合は、カスタムランタイムでWASMをパッケージ化します:

```rust
// wasm32-wasi ターゲット向けにコンパイル
pub fn lambda_handler(event: LambdaEvent<Value>) -> Result<Value> {
    let data = event.payload["data"].as_str().unwrap();
    let result = process_data(data); // WASMサンドボックス内で実行
    Ok(json!({"processed": result}))
}
```

グローバルなレイテンシ最適化にはWorkersを、AWSエコシステムとの深い統合にはLambdaを選びましょう。

## WASI（WebAssembly System Interface）を使ってファイルシステムやネットワークリソースにアクセスする

WASIは、ケーパビリティベースのセキュリティを通じて制御されたシステムアクセスを提供します。従来のコンテナとは異なり、WASIモジュールは明示的に許可されない限り、いかなるホストリソースにもアクセスできません。

**現在のWASIの制約**: WASIp1にはネットワーキングやソケットのサポートがありません。しかし2024年初頭にリリースされたWASIp2では、`wasi-http`によるHTTPクライアント/サーバーや、`wasi-keyvalue`によるキーバリューストアが追加されています。

**セキュリティ上の利点**: ホストのファイルシステムへのアクセスには`preopens`を通じた明示的な許可が必要です。`/app`へのアクセスを許可されたWASMモジュールは、`/etc/passwd`やその他のホストパスを読み取ることはできません:

```javascript
const wasi = new WASI({
  version: 'preview1',
  preopens: {
    '/app': '/var/app',           // WASM側からは/appに見え、/var/appにマッピングされる
    '/data': '/mnt/data'          // WASM側からは/dataに見え、/mnt/dataにマッピングされる
  }
});
```

このケーパビリティモデルにより、従来のアプリケーションを悩ませてきたディレクトリトラバーサル脆弱性のクラス全体を防ぐことができます。

## DockerとKubernetesオーケストレーションでWebAssemblyマイクロサービスをデプロイする

WASMによってコンテナ密度は大幅に向上します。典型的なNode.jsマイクロサービスコンテナは200〜500MBですが、同等のWASMモジュール（ランタイム込み）は10〜50MBです。

wasmtimeランタイムを使ったDocker:

```dockerfile
FROM scratch
COPY --from=wasmtime:latest /usr/bin/wasmtime /wasmtime
COPY api.wasm /app/
ENTRYPOINT ["/wasmtime", "/app/api.wasm"]
```

runwasiによるKubernetesネイティブなWASMスケジューリング:

```yaml
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: wasmtime
handler: wasmtime
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: wasm-api
spec:
  template:
    spec:
      runtimeClassName: wasmtime
      containers:
      - name: api
        image: ghcr.io/myorg/wasm-api:v1.0.0
        resources:
          requests:
            memory: "32Mi"    # 従来型コンテナよりも大幅に少ない
            cpu: "100m"
```

CNCFのレポートによると、同一ハードウェア上でWASMコンテナは従来のLinuxコンテナと比較して3〜5倍高いPod密度を実現できます。

## 画像処理や暗号処理などCPU負荷の高いタスクに向けたWebAssemblyのパフォーマンス最適化

WASMは計算負荷の高い処理において、通常ネイティブパフォーマンスの70〜90%を達成します。このパフォーマンスの差は、根本的な制約ではなく、境界チェックとJITコンパイルのオーバーヘッドに起因します。

**主要な最適化**: 状態の分離を維持しながら、コンパイル結果をリクエスト間で共有します:

```rust
// グローバルなエンジンとモジュール（一度だけコンパイルされる）
lazy_static! {
    static ref ENGINE: Engine = Engine::default();
    static ref MODULE: Module = Module::from_file(&ENGINE, "crypto.wasm").unwrap();
}

// リクエストごとの分離
fn handle_request(data: &[u8]) -> Result<Vec<u8>> {
    let mut store = Store::new(&ENGINE, ());
    let instance = Instance::new(&mut store, &MODULE, &[])?;
    let hash_fn = instance.get_typed_func::<(i32, i32), i32>(&mut store, "sha256")?;

    // メモリ安全性: 境界チェックによりバッファオーバーフローを防ぐ
    let result = hash_fn.call(&mut store, (data.as_ptr() as i32, data.len() as i32))?;
    Ok(extract_result(&mut store, result))
}
```

**パフォーマンスデータ**: ベンチマークでは、WASMの画像フィルターがメモリ安全性を完全に維持しながらネイティブ速度の75〜85%で動作することが示されています。暗号処理においては、V8の共有コードキャッシュにより、最小限のコンパイルオーバーヘッドで1秒あたり数千回のハッシュ演算が可能です。

無限ループを防ぐためにfuel meteringを有効にします:

```rust
let mut config = Config::new();
config.consume_fuel(true);
let mut store = Store::new(&engine, ());
store.fuel_consumed().unwrap(); // 実行コストを追跡する
```

## まとめ

• **WASI準拠が求められるサーバーワークロードにはWasmtimeを、軽量な組み込みにはWasmerを検討してください**。どちらが自分のCPU負荷の高いコードに対してより良いパフォーマンスを発揮するかは、両方をベンチマークして判断しましょう。

• **コールドスタートを5ミリ秒未満に抑え、グローバルなレイテンシを最適化したい場合はCloudflare Workers上にWASMをデプロイしてください**。S3・DynamoDBなど他のAWSサービスとの深い統合が必要な場合にのみ、WASM対応のAWS Lambdaを選んでください。

• **共有のEngine/Moduleパターンと、リクエストごとのStore/Instance分離を組み合わせて実装してください**。これにより、メモリ安全性を維持しながら高同時実行シナリオで最適なパフォーマンスを実現できます。

• **WASI preopensを使って必要最小限のファイルシステムアクセスのみを許可し、fuel meteringを有効にして暴走実行を防いでください**。このケーパビリティベースのセキュリティモデルは、脆弱性のクラス全体を排除します。

• **純粋な計算タスクにはWASMを使い（ネイティブパフォーマンスの70〜90%を達成）、I/O処理はホストランタイム側に残してください**。ホットパスをプロファイリングし、CPU負荷の高い関数をWASMモジュールに移行しましょう。

サーバーサイドWASMのエコシステムは、実験段階を超えて成熟しています。画像処理、暗号処理、あるいは信頼できないコードを大規模に実行しているのであれば、WebAssemblyは従来のコンテナでは実現できないセキュリティ保証を内蔵しながら、本番運用可能なパフォーマンスを提供します。

## 参考文献

- [Research on WebAssembly Runtimes: A Survey](https://arxiv.org/html/2404.12621v1)
- [Bonviewpress](https://ojs.bonviewpress.com/index.php/AAES/article/download/4965/1367/29227)
- [GitHub - appcypher/awesome-wasm-runtimes: A list of webassemby runtimes](https://github.com/appcypher/awesome-wasm-runtimes)
- [wasmtime-demos/nodejs/README.md at main · bytecodealliance/wasmtime-demos](https://github.com/bytecodealliance/wasmtime-demos/blob/main/nodejs/README.md)
- [Develop with WasmEdge, Wasmtime, and Wasmer Invoking MongoDB, Kafka, and Oracle: WASI Cycles, an Open Source, 3D WebXR Game | by Paul Parkinson | Oracle Developers | Medium](https://medium.com/oracledevs/develop-with-wasmedge-wasmtime-and-wasmer-invoking-mongodb-kafka-and-oracle-wasi-cycles-an-ad2302fe961a)
- [Wasmtime](https://wasmtime.dev/)
- [WASI and the WebAssembly Component Model: Current Status - eunomia](https://eunomia.dev/blog/2025/02/16/wasi-and-the-webassembly-component-model-current-status/)
- [Outside the web: standalone WebAssembly binaries using Emscripten · V8](https://v8.dev/blog/emscripten-standalone-wasm)
- [Wasmtime In-Depth Tutorial | wasmRuntime.com](https://wasmruntime.com/en/tutorials/wasmtime)
- [Choosing a WebAssembly Run-Time](https://blog.colinbreck.com/choosing-a-webassembly-run-time/)
- [AWS Lambda vs. Cloudflare Workers Detailed Comparison](https://5ly.co/blog/aws-lambda-vs-cloudflare-workers/)
- [How can serverless computing improve performance? | Lambda performance | Cloudflare](https://www.cloudflare.com/learning/serverless/serverless-performance/)
- [The Rise of Serverless: Powering Modern Apps with AWS Lambda and Cloudflare Workers | by Aayush Tiwari | Medium](https://medium.com/@aayush71727/the-rise-of-serverless-powering-modern-apps-with-aws-lambda-and-cloudflare-workers-c044020eff6c)
- [Going Serverless With Cloudflare Workers — Smashing Magazine](https://www.smashingmagazine.com/2019/04/cloudflare-workers-serverless/)
- [Best Cloudflare Workers alternatives in 2026 | Blog — Northflank](https://northflank.com/blog/best-cloudflare-workers-alternatives)
- [Serverless Performance: Cloudflare Workers, Lambda and Lambda@Edge](https://blog.cloudflare.com/serverless-performance-comparison-workers-lambda/)
- [AWS Lambda vs Cloudflare Workers | Upstash Blog](https://upstash.com/blog/aws-lambda-vs-cloudflare-workers)
- [Python Workers redux: fast cold starts, packages, and a uv-first workflow](https://blog.cloudflare.com/python-workers-advancements/)
- [Taking a look at Cloudflare Workers](https://willhamill.com/2019/01/23/taking-a-look-at-cloudflare-workers)
- [Cloudflare’s Workers enable containerless cloud computing powered by V8 Isolates and WebAssembly](https://hub.packtpub.com/cloudflares-workers-enable-containerless-cloud-computing-powered-by-v8-isolates-and-webassembly/)
- [Introduction · WASI.dev](https://wasi.dev/)
- [WebAssembly System Interface (WASI) | Node.js v25.6.1 Documentation](https://nodejs.org/api/wasi.html)
- [WASI Introduction](https://wasmbyexample.dev/examples/wasi-introduction/wasi-introduction.all.en-us)
- [GitHub - WebAssembly/WASI: WebAssembly System Interface](https://github.com/WebAssembly/WASI)
- [GitHub - WebAssembly/wasi-filesystem: Filesystem API for WASI](https://github.com/WebAssembly/wasi-filesystem)
- [What is WASI? | Fastly](https://www.fastly.com/learning/serverless/what-is-wasi)
- [WASI: a New Kind of System Interface - InfoQ](https://www.infoq.com/presentations/wasi-system-interface/)
- [What’s The State of WASI?](https://www.fermyon.com/blog/whats-the-state-of-wasi)
- [Wasm, WASI, Wagi: What are they?](https://www.fermyon.com/blog/wasm-wasi-wagi)
- [WebAssembly, WASI, and the Component Model](https://www.fermyon.com/blog/webassembly-wasi-and-the-component-model)
