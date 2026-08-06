#!/bin/bash
# Execute a shell command and return its output
# @param command:string:Shell command to execute (required)
# @param timeout:integer:Timeout in seconds (default: 30)

command="${command:?command is required}"
timeout_val="${timeout:-30}"

timeout "$timeout_val" bash -c "$command" 2>&1
