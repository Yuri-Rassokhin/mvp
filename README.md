# What is MVP
Framework for quick development of a minimal viable product as HTTP mesh of loosely coupled microservices



## How It Works

MVP analyzes codebase of your Python project and turns specified functions into HTTP endpoints.
To specify which functions to expose, create a simple YAML file called **contract** in the root directory of your project.

As a toy example, let's create a simple Python file:

```python
def increment(number: int)
	return number+1
```

To turn it to HTTP endpoint, put the following contract `contract.yaml` in the same (or higher) directory:

```yaml
name: increment
description: highly optimized library for mathematical increment
endpoints:
     - increment
```

Then run `mvp add ./contract.yaml`. MVP will recursively scan current directory, finds `increment` function in the Python file, converts it to an HTTP endpoint, and exposes the endpoint. You can check its status:

```bash
dev> mvp ls
────────────────────────────────────────
Component    increment
Instance     2caf39411e18472980b3ece78ecb50b9
Description  highly optimized library for mathematical increment
http://10.0.0.200:8500/increment { "number": int }
```

MVP automatically determined the lowest port starting from 8500 and exposed `increment` function as a conventional HTTP endpoint. You can now call it from CLI:

```bash
dev> mvp call 2caf39411e18472980b3ece78ecb50b9 increment '{"number":42}'
43
```

You can also manage your endpoints from any Python code.

While the example above is trivial, MVP hides a lot of functionality for convenience of a developer:
- Automated assignment of HTTP ports
- Identification and notification about potentially unsafe code in the global scope (anything except import, declaration, and constant assignment)
- Automated transformation of the code as a future endpoint. If your function lacks `return` statement, then MVP adds `return 200` for clarity. If your file includes `if __name__ == __main__` construct, MVP removes it

MVP roadmap has rich functionality in the roadmap - from a mock layer of your component through in-flight performance benchmarking to automated drawing of the communication dagram of your architecture, so stay tuned.



## Why MVP?

While conventional HTTP frameworks such as FastAPI follow the **functional** paradigm, MVP follows **declarative** paradigm, taking you to a higher - and more pleasant - level of abstraction.
Instead of thinking HOW you code works in detail, you can focus on WHAT you code does at higher level - this is why the concept of a contract is crucial in MVP.

Even more importantly, a contract provides MVP framework with complete information about your project.
As MVP knows what fucntions must be exposed as HTTP, it can track correctness of their behaviour, provide fallback tier if production endpoint has gone offline, reload the endpoint if its code has changed, pull the code directly from GitHub repo, measure response latencies, visualize a diagram of the endpoints and their intercommunication, and many more.

Finally and most importantly, MVP's contracts literally set a contract for your collaborators. No matter what you're doing to your code, your teammates will be provided with the functionality guaranteed by the contract you announced via MVP. This turns collaborative software development to a loosely-coupled work, where none of you depends on another one, and waits for no one. You just declare your MVP contracts across your team and enjoy developing of your own part of code.


## Requirements

MVP sets a few restrictions for the Python project to be convertible.

1. Avoid relative `import`
2. Default values in a functions signature are not converted
3. HTTP server code should be avoided in the code you're converting - which is quite obvious given the fact that MVP provides exactly this, HTTP server functionality :)

Additionally, functions being converted are _recommended_ to have signatures consisting of basic types - scalars, arrays, lists, dictionaries, sets. However, this isn't mandatory. If you really want to pass a thread or socket via HTTP, nothing can stop you.

