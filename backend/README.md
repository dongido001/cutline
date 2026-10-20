
### Concurrent video jobs

`MOVIE_VIDEO_WORKER_CONCURRENCY` controls video worker threads and defaults to `1`.
Set it to `3` for overlapping FlowKit video jobs across projects. FlowKit still
applies its own submission throttling. Restart the movie worker after changing
this setting, once active jobs finish. Queue positions describe waiting jobs,
not an estimated start time.

### Concurrent image jobs

`MOVIE_IMAGE_WORKER_CONCURRENCY` controls image worker threads and defaults to `1`.
Raise it together with the ChatGPT bridge's `CHATGPT_WEB_MAX_CONCURRENT_REQUESTS`
(each image job uses its own browser tab). Keep the Cutline value at or below
the bridge cap so Huey does not pile up requests the bridge will only serialize.
