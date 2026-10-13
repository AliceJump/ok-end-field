# Map review comments to threads

REST review comments have numeric `id`, opaque `node_id`, and `in_reply_to_id`; GraphQL review comments use the REST `node_id` as their `id`. Enumerate every `reviewThreads` page and every thread's `comments` pages, then match those IDs to obtain the thread ID. When a reply is needed under the skill's reply rules, use the top-level REST comment ID. Use the GraphQL thread ID only for read-only state and comment queries.

For location evidence, record `path`, `line`, `originalLine`, and `isOutdated` on threads, and REST `line`/`original_line` on comments. A current thread normally has a current line; an outdated thread may have only an original line. Fall back to the original location to find the old code, then verify the finding against the current code. Neither an outdated flag nor a moved line proves resolution.

When implementing or changing mapping/API helpers, verify REST-to-GraphQL mapping with at least one current thread and one outdated thread, including the original-line fallback.

## Pagination

As specified in the [GitHub CLI manual](https://cli.github.com/manual/gh_api), `gh api graphql --paginate` requires a query variable `$endCursor: String` and `pageInfo { hasNextPage endCursor }`; pass the cursor as `after: $endCursor`. Use separate queries for the PR's threads and each thread's comments; one automatically paginated query cannot independently advance all nested connections.

For thread inventory, request:

```graphql
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $endCursor) {
        nodes { id isResolved isOutdated path line originalLine }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
```

For each thread, separately request:

```graphql
query($thread: ID!, $endCursor: String) {
  node(id: $thread) {
    ... on PullRequestReviewThread {
      comments(first: 100, after: $endCursor) {
        nodes { id databaseId replyTo { id } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
```

Read `isResolved` to report the actual platform state. Never resolve or reopen a thread yourself, including when the original reviewer accepted the fix but the platform still shows it open. Leave closure to the original reviewer and report any remaining open state. Keep native-command quoting compatible with the Windows PowerShell guidance in `SKILL.md`.
