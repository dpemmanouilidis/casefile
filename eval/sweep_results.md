| Row | n | Citation precision | 1st-attempt pass | Post-retry pass | Fallback rate | Check 6 pass rate | Evidence mention rate | Mean attempts | Wall (s) | VRAM (MiB) | num_ctx |
|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3.5:9b Q4_K_M think:false | 16 | 100% | 100% | 100% | 0% | 100% | 47% | 1.0 | 97 | 8302 | 8192 |
| qwen3.5:9b-q8_0 Q8_0 think:false | 16 | 100% | 100% | 100% | 0% | 100% | 47% | 1.0 | 136 | 11623 | 8192 |
| qwen2.5:3b Q4_K_M think:false | 16 | 100% | 12% | 12% | 88% | 100% | 100% | 2.8 | 144 | 4205 | 8192 |
| qwen3.5:9b Q4_K_M think:true | 16 | 100% | 0% | 0% | 100% | n/a | 100% | 3.0 | 526 | 8295 | 8192 |
