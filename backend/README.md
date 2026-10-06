
### Concurrent video jobs

`MOVIE_VIDEO_WORKER_CONCURRENCY` controls video worker threads and defaults to `1`.
Set it to `3` for overlapping FlowKit video jobs across projects. FlowKit still
applies its own submission throttling; image and other worker counts are unchanged.
Restart the movie worker after changing this setting, once active jobs finish.
Queue positions describe waiting jobs, not an estimated start time.
