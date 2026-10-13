# Changelog

## 0.1.0 (2026-10-13)


### Features

* add size-limited feed fetching with redirect and private address checks ([48dfc63](https://github.com/Garulf/podpocket/commit/48dfc632a54c5c2576e1117bb652e843f3d9dac5))
* add stable podcast and episode ids and wire time formats ([8604159](https://github.com/Garulf/podpocket/commit/86041594f4d20fb6facb3bf4c01e679b12f105a4))
* deliver new episodes, add-by-url and opml import on the refresh api ([35ee54c](https://github.com/Garulf/podpocket/commit/35ee54cfbdf2577995c1ebbf439e259747aa936f))
* ingest feeds with stable ids, backoff and scheduled refresh ([96352c0](https://github.com/Garulf/podpocket/commit/96352c077f690b83ff7cbf4ef7c8fc68a1e29cb9))
* parse podcast feeds into episodes with messy-feed tolerance ([02832e7](https://github.com/Garulf/podpocket/commit/02832e7a81e5a264c2d1086f5a30e2a4c99a902d))
* scaffold server with database, migrations and health check ([a0bcef9](https://github.com/Garulf/podpocket/commit/a0bcef9be7466b60735411f1c8b718b317f7166f))
* search podcasts locally and through the itunes directory ([5d9c49a](https://github.com/Garulf/podpocket/commit/5d9c49aa021f5763267e034e560592656dbf6e7f))
* serve discover layout, top chart, artwork and colors on the static api ([9abf078](https://github.com/Garulf/podpocket/commit/9abf078c60121a94dfc38d0cadb890144441438b))
* serve podcast, episode and show notes data on the cache api ([9ea9455](https://github.com/Garulf/podpocket/commit/9ea945512c6a61af575134943aa1d41b7d4d6807))


### Bug Fixes

* bound decompression, validate addresses at connect time and wrap url errors ([71b75c9](https://github.com/Garulf/podpocket/commit/71b75c999830cf8e284be5039dfc02f91400eb16))
* coerce blank feed categories and out of range integers ([6e89aaa](https://github.com/Garulf/podpocket/commit/6e89aaa30474a1c2011494bbe96aa6d74966c6fe))
* give outbound requests a 30 second budget and name network errors ([70737d4](https://github.com/Garulf/podpocket/commit/70737d406be4538939ebf8bb487478af11ae82f9))
* harden artwork rendering and keep colors and discover answering ([014d71f](https://github.com/Garulf/podpocket/commit/014d71fff368fd8a8fd19ec28814374cd1a44689))
* keep uv.lock in step with releases and check the image boots ([c9081e5](https://github.com/Garulf/podpocket/commit/c9081e536d22b728ee133ae0520042790adf0b3e))
* let overlapping opml polls share finished imports ([6bf9e20](https://github.com/Garulf/podpocket/commit/6bf9e2092f2f5350cef3693c6731e316af6f5ef5))
* pair update tokens by position and answer unknown episodes with the podcast ([db02d34](https://github.com/Garulf/podpocket/commit/db02d34d156c8e214b7df688854a5bb3aa134737))
* reject invalid durations and sizes and tidy feed urls ([9e93a4c](https://github.com/Garulf/podpocket/commit/9e93a4c2c2e8d44ecff456d5d32f34c10537688f))
* release the database during feed fetches and back off on any failure ([b3d9ddd](https://github.com/Garulf/podpocket/commit/b3d9ddd7bb97e96c51be896252189e1a87072c7b))
* run the lint env on python 3.13 ([27433ea](https://github.com/Garulf/podpocket/commit/27433ea34a48de307d66b0108c90cae0bdece97d))
* size the database pool for concurrent refreshes ([9fbde8f](https://github.com/Garulf/podpocket/commit/9fbde8fb0646329ae144540182ba861d2353fc09))
* test scheduler lifecycle and isolate refresh failures ([5a7f751](https://github.com/Garulf/podpocket/commit/5a7f751f624cf4c06611160af42c7da9ecf9ce98))
* tighten feed url normalization and reject naive datetimes ([9c52c73](https://github.com/Garulf/podpocket/commit/9c52c7302262afe68d287581ab18c45977639323))
* tolerate malformed itunes directory data ([db817fc](https://github.com/Garulf/podpocket/commit/db817fc99a5bdeb63ba8eee0fe938fcf29eded42))
* try every vetted address when connecting ([ce95093](https://github.com/Garulf/podpocket/commit/ce950939e9535308792c51a335f15a7f04c4b770))


### Documentation

* show the environment for a local run ([aec4ab9](https://github.com/Garulf/podpocket/commit/aec4ab90ecd9b037e0039bdbb85ef9b002cd7df9))
