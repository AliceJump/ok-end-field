# Map review comments to threads

REST review comments have numeric `id`, opaque `node_id`, and `in_reply_to_id`; GraphQL review comments use the REST `node_id` as their `id`. Enumerate every `reviewThreads` page and every thread's `comments` pages, then match those IDs to obtain the thread ID. Reply through the top-level REST comment ID; resolve through the GraphQL thread ID.

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

Fetch the selected thread's current `isResolved` just before mutation. If already resolved, skip it. Otherwise confirm the disposition, call `resolveReviewThread(input: {threadId: ...})`, and inspect the returned thread's `isResolved`. Keep native-command quoting compatible with the Windows PowerShell guidance in `SKILL.md`.
