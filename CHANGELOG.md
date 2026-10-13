# Changelog

## [0.3.0](https://github.com/Garulf/podpocket/compare/v0.2.0...v0.3.0) (2026-10-13)


### Features

* authenticate gpodder clients with the account password ([a6a7e58](https://github.com/Garulf/podpocket/commit/a6a7e58f4cb29066c5ea1341c189981618bc0cf3))
* merge gpodder episode actions into episode state ([06aa4c3](https://github.com/Garulf/podpocket/commit/06aa4c38b05693ac5a8accd5653aa0bc06e42063))
* serve and accept gpodder subscription changes ([1babe75](https://github.com/Garulf/podpocket/commit/1babe75928139580eadd99c2035b58c8f27621bc))
* serve episode progress as gpodder episode actions ([3cc3c9f](https://github.com/Garulf/podpocket/commit/3cc3c9fc489f3f88961a258342e05601a76b3567))


### Bug Fixes

* answer 422 for lone surrogates in cache search bodies ([2085236](https://github.com/Garulf/podpocket/commit/20852361f2e99c956de497b6606e97a9132f8e7b))
* bound and narrow gpodder episode actions ([e13fe9b](https://github.com/Garulf/podpocket/commit/e13fe9b119111afa5f0f7dcc17bb44b4b76cea8c))
* cache verified gpodder credentials ([3594e00](https://github.com/Garulf/podpocket/commit/3594e002a20fd5f9476c21ce047444d111dc752d))
* harden gpodder credential cache and subscription conflicts ([1587eab](https://github.com/Garulf/podpocket/commit/1587eab31433b82afd289e5064f8900682aa6a39))
* keep music assistant signed in between its syncs ([48c1fdb](https://github.com/Garulf/podpocket/commit/48c1fdbb1e265ba4fa2d6161dc032c8f19022a66))
* mark finished episodes played and keep lookups out of the write lock ([d0bbcbf](https://github.com/Garulf/podpocket/commit/d0bbcbf1cee16107e3bae7dd563e47eac1b772fe))
* reject invalid gpodder action timestamps and collapse duplicate actions ([a8c454e](https://github.com/Garulf/podpocket/commit/a8c454eaf102ade11c94eb187dd336db90b7f4f7))
* write the change log in one statement per request ([eda5eff](https://github.com/Garulf/podpocket/commit/eda5efff25c96323d09592aded0b922f6611cca1))


### Documentation

* describe manual sync and the remembered sign-in ([d16a608](https://github.com/Garulf/podpocket/commit/d16a608a0068eed801711aa44872590ff6b0b6d5))
* describe the music assistant setup ([f2bb173](https://github.com/Garulf/podpocket/commit/f2bb173c9720454dd4451f992d4166d74a38c2f9))
* explain music assistant sync timing and credential limits ([a480a91](https://github.com/Garulf/podpocket/commit/a480a9126f3ad540f6c144ff0b8917e64f44b88d))

## [0.2.0](https://github.com/Garulf/podpocket/compare/v0.1.0...v0.2.0) (2026-10-13)


### Features

* add account and sync tables ([0fed8cd](https://github.com/Garulf/podpocket/commit/0fed8cdef6b1f5407ff768439b1bb618473a3b05))
* add change log writer, protobuf body helpers and a request size cap ([b5cf943](https://github.com/Garulf/podpocket/commit/b5cf943a536b4f0c10ca6b574f9d7c3b8a7bd6fe))
* add password hashing, user management and the user admin cli ([a4e010d](https://github.com/Garulf/podpocket/commit/a4e010d4f087a1fdfd739caf3235a40800048cfd))
* add size-limited feed fetching with redirect and private address checks ([48dfc63](https://github.com/Garulf/podpocket/commit/48dfc632a54c5c2576e1117bb652e843f3d9dac5))
* add stable podcast and episode ids and wire time formats ([8604159](https://github.com/Garulf/podpocket/commit/86041594f4d20fb6facb3bf4c01e679b12f105a4))
* deliver new episodes, add-by-url and opml import on the refresh api ([35ee54c](https://github.com/Garulf/podpocket/commit/35ee54cfbdf2577995c1ebbf439e259747aa936f))
* ingest feeds with stable ids, backoff and scheduled refresh ([96352c0](https://github.com/Garulf/podpocket/commit/96352c077f690b83ff7cbf4ef7c8fc68a1e29cb9))
* issue and verify access and refresh tokens ([79ec002](https://github.com/Garulf/podpocket/commit/79ec00211a7f2f1de582c20a082f0e55ced18b55))
* merge episode state with per-field last writer wins ([b0b0a7a](https://github.com/Garulf/podpocket/commit/b0b0a7a270b8f67eb5834f2981d46a2f9436f2b1))
* merge playlists, bookmarks and device stats ([ac57d9c](https://github.com/Garulf/podpocket/commit/ac57d9ce524907c80285c9e68f4cdaa8cf2da405))
* merge subscriptions and folders and refresh subscribed podcasts ([b5d0367](https://github.com/Garulf/podpocket/commit/b5d03673e3f5b1907381dd5a706b12e80cc31c07))
* parse podcast feeds into episodes with messy-feed tolerance ([02832e7](https://github.com/Garulf/podpocket/commit/02832e7a81e5a264c2d1086f5a30e2a4c99a902d))
* scaffold server with database, migrations and health check ([a0bcef9](https://github.com/Garulf/podpocket/commit/a0bcef9be7466b60735411f1c8b718b317f7166f))
* search podcasts locally and through the itunes directory ([5d9c49a](https://github.com/Garulf/podpocket/commit/5d9c49aa021f5763267e034e560592656dbf6e7f))
* serve discover layout, top chart, artwork and colors on the static api ([9abf078](https://github.com/Garulf/podpocket/commit/9abf078c60121a94dfc38d0cadb890144441438b))
* serve full sync lists for podcasts, episodes, playlists, bookmarks and stars ([510cf23](https://github.com/Garulf/podpocket/commit/510cf2306c1549ec795fb64a7da3be89e736a59b))
* serve incremental sync and last sync time ([90492f4](https://github.com/Garulf/podpocket/commit/90492f406d3a23aad3bd0a292ddd7f9f693b7b39))
* serve podcast, episode and show notes data on the cache api ([9ea9455](https://github.com/Garulf/podpocket/commit/9ea945512c6a61af575134943aa1d41b7d4d6807))
* serve sign-in, token refresh and plus status on the api ([f16ab25](https://github.com/Garulf/podpocket/commit/f16ab2591b050a7711be88a1523c2ea9299b1653))
* sync listening history ([4e5fbfa](https://github.com/Garulf/podpocket/commit/4e5fbfabe0603cfee6721c7e38085c6c8be7f50b))
* sync named settings and listening stats ([338349a](https://github.com/Garulf/podpocket/commit/338349a5ecf16da6dc07c6c4da930dc146868ef4))
* sync up next over json and protobuf ([5ac30aa](https://github.com/Garulf/podpocket/commit/5ac30aaf5dddf5d64fb2029ddcacada5db245599))
* vendor the sync protobuf schema and require a secret key ([9e07bd3](https://github.com/Garulf/podpocket/commit/9e07bd3b01305e215cef14d34fcf95cdbfaecf16))


### Bug Fixes

* answer 401 when the account disappears mid-request ([e3e038a](https://github.com/Garulf/podpocket/commit/e3e038aa015c28b637483f108e9ff0d08a3a5a2e))
* answer 422 for lone surrogates in history, episode and progress bodies ([a944a01](https://github.com/Garulf/podpocket/commit/a944a013c60e598d05a755b42f67c67ffee6536b))
* answer malformed token requests with 400 and pin the response keys ([4a2bc31](https://github.com/Garulf/podpocket/commit/4a2bc318a076f504172751f148f8e386da8d9312))
* apply every up next change in order and clamp client clocks to now ([296d461](https://github.com/Garulf/podpocket/commit/296d4615655eaddf5475d4e95c0aff66436d5e6f))
* bound decompression, validate addresses at connect time and wrap url errors ([71b75c9](https://github.com/Garulf/podpocket/commit/71b75c999830cf8e284be5039dfc02f91400eb16))
* bound full sync episode lookups and clamp json positions ([fdf0054](https://github.com/Garulf/podpocket/commit/fdf0054c49cd9159732fc68a9f3b2df65efb2261))
* bound the access token lifetime to one minute through one day ([3c5fe36](https://github.com/Garulf/podpocket/commit/3c5fe366a7a55e46f2fba2c8c804d246798d91a5))
* coerce blank feed categories and out of range integers ([6e89aaa](https://github.com/Garulf/podpocket/commit/6e89aaa30474a1c2011494bbe96aa6d74966c6fe))
* give outbound requests a 30 second budget and name network errors ([70737d4](https://github.com/Garulf/podpocket/commit/70737d406be4538939ebf8bb487478af11ae82f9))
* guard podcast merges and pin the content length test ([da1442d](https://github.com/Garulf/podpocket/commit/da1442ddd0bd82c506ae90f4d4cec9f7c91f4fd9))
* harden artwork rendering and keep colors and discover answering ([014d71f](https://github.com/Garulf/podpocket/commit/014d71fff368fd8a8fd19ec28814374cd1a44689))
* harden login against odd input and timing and tidy the user cli ([411bcf3](https://github.com/Garulf/podpocket/commit/411bcf3417901ff7565ce7323cd28383655c8436))
* harden the episode merge for every caller ([44d2f8c](https://github.com/Garulf/podpocket/commit/44d2f8c2a04ff04225fbc5c2cb0497ca0dafdf63))
* harden the size cap and pin change stamp ordering ([3e46b0b](https://github.com/Garulf/podpocket/commit/3e46b0bdbc22c8998d54f593909cefa81cdfeacc))
* hash passwords off the database and throttle logins ([2bdd6bd](https://github.com/Garulf/podpocket/commit/2bdd6bde86bd5491b7a28fd438839426ee0c2264))
* keep every refresh token in a racing pair valid ([7cc8758](https://github.com/Garulf/podpocket/commit/7cc8758b9251cd35af1d46bb1078870fe624773a))
* keep uv.lock in step with releases and check the image boots ([c9081e5](https://github.com/Garulf/podpocket/commit/c9081e536d22b728ee133ae0520042790adf0b3e))
* let overlapping opml polls share finished imports ([6bf9e20](https://github.com/Garulf/podpocket/commit/6bf9e2092f2f5350cef3693c6731e316af6f5ef5))
* look up a refresh token before taking the write lock ([0ffbb53](https://github.com/Garulf/podpocket/commit/0ffbb537f285756b754836c6e4b68028bb802c77))
* make refresh token rotation race safe ([832f532](https://github.com/Garulf/podpocket/commit/832f5328f03e087e8aa793ac97a090aedcd147c0))
* never echo request input in validation errors ([41953b4](https://github.com/Garulf/podpocket/commit/41953b4cc77a82b82c443beb4e8507c348146784))
* pair update tokens by position and answer unknown episodes with the podcast ([db02d34](https://github.com/Garulf/podpocket/commit/db02d34d156c8e214b7df688854a5bb3aa134737))
* reject invalid durations and sizes and tidy feed urls ([9e93a4c](https://github.com/Garulf/podpocket/commit/9e93a4c2c2e8d44ecff456d5d32f34c10537688f))
* release the database during feed fetches and back off on any failure ([b3d9ddd](https://github.com/Garulf/podpocket/commit/b3d9ddd7bb97e96c51be896252189e1a87072c7b))
* run the lint env on python 3.13 ([27433ea](https://github.com/Garulf/podpocket/commit/27433ea34a48de307d66b0108c90cae0bdece97d))
* sign the caller in before reading the request body ([f29e5a9](https://github.com/Garulf/podpocket/commit/f29e5a96a70eaf3d83ed5a505681d33a6973a94b))
* size the database pool for concurrent refreshes ([9fbde8f](https://github.com/Garulf/podpocket/commit/9fbde8fb0646329ae144540182ba861d2353fc09))
* tag releases and images with the plain version ([ef2a347](https://github.com/Garulf/podpocket/commit/ef2a34790d3e8cd2c03f471b23d115cb3e963f2c))
* test scheduler lifecycle and isolate refresh failures ([5a7f751](https://github.com/Garulf/podpocket/commit/5a7f751f624cf4c06611160af42c7da9ecf9ce98))
* tighten feed url normalization and reject naive datetimes ([9c52c73](https://github.com/Garulf/podpocket/commit/9c52c7302262afe68d287581ab18c45977639323))
* tolerate malformed itunes directory data ([db817fc](https://github.com/Garulf/podpocket/commit/db817fc99a5bdeb63ba8eee0fe938fcf29eded42))
* try every vetted address when connecting ([ce95093](https://github.com/Garulf/podpocket/commit/ce950939e9535308792c51a335f15a7f04c4b770))


### Documentation

* correct the secret key notes and describe sync behaviour ([1e7de45](https://github.com/Garulf/podpocket/commit/1e7de4579c09d74b3fc7128c29388c194db1fc95))
* describe login throttling and the new settings ([8a2a66f](https://github.com/Garulf/podpocket/commit/8a2a66fb9166b5c185ae8431ed1b6a1450e37fef))
* document accounts, the secret key and the user cli ([e9f5010](https://github.com/Garulf/podpocket/commit/e9f5010f540b4e3a0cfffaff76fb6fa6b3831cf3))
* show the environment for a local run ([aec4ab9](https://github.com/Garulf/podpocket/commit/aec4ab90ecd9b037e0039bdbb85ef9b002cd7df9))

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
