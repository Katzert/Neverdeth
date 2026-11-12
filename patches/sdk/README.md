# ReXGlue SDK Patches

Copy the contents of `rex/` over the SDK `include/` directory **before compiling**:

```bat
xcopy /E /Y patches\sdk\rex <SDK>\include\rex\
```

## rex/ppc/context.h — Eliminate `ldmxcsr` pipeline stalls

`FPSCRRegister::enableFlushMode()` / `disableFlushMode()` (and their `Unconditional`
variants) become **flag-only**: they update the software `csr` mirror without writing
`MXCSR`. `setcsr()` and `InitHost()` keep `FTZ|DAZ` enabled.

Rationale: codegen emits ~34,754 FlushMode transitions across the generated PPC
functions (one per VMX instruction). With the unmodified header each transition
executes `ldmxcsr`, serializing the CPU pipeline (283 `ldmxcsr` instructions per
object file reduced to 0).

Back up `<SDK>\include\rex\ppc\context.h` manually before applying the overlay if you need to restore the unmodified SDK header later.
