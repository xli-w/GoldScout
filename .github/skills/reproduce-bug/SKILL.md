---
name: reproduce-bug
description: "Reproduce a portfolio evidence defect with a minimal offline regression."
disable-model-invocation: true
argument-hint: "Failure, traceback, or expected behavior"
---
Locate the controlling code for the reported failure. State one falsifiable hypothesis and add or select the smallest offline check that distinguishes it. Run through the isolated test runner. Report observed versus expected behavior and the check result; do not implement the production fix unless requested.