# Minimal SDK function plugin

Install the matching Akane Plugin SDK 0.14.0 and CapCore 0.1.3 release wheels,
then `pip install .` in a clean virtual environment. Run
`python -m unittest discover -s tests -v`. The project imports no Akane host code.

For Akane runtime use, pass this source directory to the existing extension
`stage_source` operation, review its returned permissions, then install that
stage. `example.math.add` becomes available with integer `a` and optional `b`
(default 1). Its integer result is the same through model and program consumers.

Permission: `capability.prompt.invoke`. The function has no side effects and
registers no event, background service, model client or delivery operation.
