# MVP
Framework for quick development of MVP as HTTP mesh of loosely coupled microservices

## How It Works

If you have a Python code that you want to add to MVP mesh as a resilient, managed HTTP endpoint,
create manifest file in root folder of your project:

`coolproject.yaml`

name: MyCoolProject
description: This project does cool things
endpoints:
     - run_me_function

And run `mvp add ./coolproject.yaml`

Your project will become available as HTTP endpoint with the path `/run_me_function`. You can check it yourself:

`mvp ls`

## Requirements

MVP requires a few restrictions for the Python project to be convertible.

1. All files that include endpoint functions of imported as dependencies:
  - Must not include executable code in global scope, except for simple initializations and imports.
  - Relative imports must use `.`
  - HTTP server code should be avoided - as MVP already converts your code to HTTP server, their conflict can cause unpredictable behaviour, highly unlikely leading to any good result.

2. Endpoint functions are _recommended_ to have flat signatures consisting of basic types (numerics, strings, arrays, lists, sets). This isn't mandatory though.


