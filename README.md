# What is MVP
Framework for quick development of a minimal viable product as HTTP mesh of loosely coupled microservices



## How It Works

MVP analyzes codebase of your Python project and turns specified functions into HTTP endpoints.
You just have to specify which functions to expose as HTTP endpoints.
To do this, you put `contract` YAML file in the root directory of your project.

As a toy example, let's create a simple Python file:

```python
def increment(number: int)
	return number+1
```

To turn in to HTTP endpoint, put the following contract 'contract.yaml' in the same (or higher) directory:

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

MVP automatically determined the lowest port starting from 8500, and exposed `increment` function as a conventional HTTP endpoint.

Now you can call it from CLI:

```bash
dev> mvp call 2caf39411e18472980b3ece78ecb50b9 increment '{"number":42}'
43
```

You can also manage your endpoints from any Python code.

While the example above is trivial, MVP hides a lot of functionality for convenience of a developer:
- Automated assignment of HTTP ports
- Identify and warn about potentially unsafe code in global scope (which is anything except import, declaration, and constant assignment)
- Automated transformation of the code as a future endpoint:
  - If your function lacks `return` statement, then MVP adds `return 200` for clarity
  - If your code includes `if __name__ == __main__` construct, MVP completely removes it

MVP road has rich functionality in the roadmap - from a mock layer of your component through in-flight performance benchmarking to automated drawing of the communication dagram of your architecture, so stay tuned.



## Requirements

MVP sets a few restrictions for the Python project to be convertible.

1. Avoid relative `import`
2. Default values in a functions signature are not converted
3. HTTP server code should be avoided in the code you're converting - which is quite obvious given the fact that MVP provides exactly this, HTTP server functionality :)

Addiontally, functions being converted are _recommended_ to have signatures consisting of basic types - scalars, arrays, lists, dictionaries, sets). However, this isn't mandatory. If you really want to pass a thread descriptor or an object address via HTTP, nothing can stop you.

