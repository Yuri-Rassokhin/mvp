<div align="center">
<h1>MVP</h1>
<b>Framework for the development of a minimal viable product as HTTP mesh of loosely coupled components</b>
</div>



# How It Works

MVP analyzes codebase of your Python project and turns specified functions into HTTP endpoints.
MVP introduces the concept of a **contract** to employ a declarative paradigm for software developent.
A contract is a simple YAML description of WHAT your code promises to do - as opposed to conventional functional paradigm where you describe HOW your code works.

As a toy example, let's create Python "project" consisting of one file:

```python
def increment(number: int)
	return number+1
```

To turn it to HTTP endpoint, create a contract `my_contract.yaml` in the same directory or higher:

```yaml
name: increment
description: highly optimized library for mathematical increment
endpoints:
     - increment
```

Now run `mvp add ./my_contract.yaml`, and MVP will recursively scan the directory, find `increment` function in the Python file, converts it to HTTP endpoint under the same name, and expose the endpoint via a vacant HTTP port. You can check the status of the endpoint:

```bash
dev> mvp ls
────────────────────────────────────────
Component    increment
Instance     2caf39411e18472980b3ece78ecb50b9
Description  highly optimized library for mathematical increment
http://10.0.0.200:8500/increment { "number": int }
```

MVP automatically determined the lowest port starting from 8500 and exposed `increment` function as a conventional HTTP endpoint with automatically determined input parameters. Now you can call it from CLI:

```bash
dev> mvp call 2caf39411e18472980b3ece78ecb50b9 increment '{"number":42}'
43
```

You can manage your endpoints from your Python code as well. In the example below, your component spins up to process just one request, and then it is terminated:

```python
import mvp

try:
	component = mvp.add("./my_contract.yaml")
	result = mvp.call(component, "increment", {"number":42})
finally:
	mvp.rm(component)
```

While the examples above are trivial, MVP hides a lot of functionality under the hood, for convenience of a developer:
- Automated assignment of HTTP ports
- Identification and notification about potentially unsafe code in the global scope (anything except import, declaration, and constant assignment)
- Automated transformation of the code as a future endpoint. If your function lacks `return` statement, then MVP adds `return 200` for clarity. If your file includes `if __name__ == __main__` construct, MVP removes it

MVP roadmap has rich functionality in the roadmap - from a mock layer of your component through in-flight performance benchmarking to automated drawing of the communication dagram of your architecture, so stay tuned.



# Why MVP?

While conventional HTTP frameworks such as FastAPI follow the **functional** paradigm, MVP follows **declarative** paradigm, taking you to a higher - and more pleasant - level of abstraction.
Instead of thinking HOW you code works in detail, you can focus on WHAT you code does at higher level - this is why the concept of a contract is crucial in MVP.

Even more importantly, a contract provides MVP framework with complete information about your project.
As MVP knows what fucntions must be exposed as HTTP, it can track correctness of their behaviour, provide fallback tier if production endpoint has gone offline, reload the endpoint if its code has changed, pull the code directly from GitHub repo, measure response latencies, visualize a diagram of the endpoints and their intercommunication, and many more.

Finally and most importantly, MVP's contracts literally set a contract for your collaborators. No matter what you're doing to your code, your teammates will be provided with the functionality guaranteed by the contract you announced via MVP. This turns collaborative software development to a loosely-coupled work, where none of you depends on another one, and waits for no one. You just declare your MVP contracts across your team and enjoy developing of your own part of code.


# Requirements

MVP sets a few restrictions for the Python project to be convertible.

1. Avoid relative `import`
2. Default values in a functions signature are not converted
3. HTTP server code should be avoided in the code you're converting - which is quite obvious given the fact that MVP provides exactly this, HTTP server functionality :)

Additionally, functions being converted are _recommended_ to have signatures consisting of basic types - scalars, arrays, lists, dictionaries, sets. However, this isn't mandatory. If you really want to pass a thread or socket via HTTP, nothing can stop you.

# Installing MVP

```bash
git clone https://github.com/Yuri-Rassokhin/mvp.git
cd mvp
pip install -e .
```

