"""
Use pythonnet to reflect on CustomerFWU2Point5.exe — list types, methods,
fields, attributes. Focus on encryption/firmware-related ones.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import clr
import System
from System.Reflection import Assembly, BindingFlags
from System import AppDomain, ResolveEventHandler

DEP_DIR = r'L:\PROJECTS\razer-joro\captures\fwu_extract\zip_contents'
PATH = DEP_DIR + r'\CustomerFWU2Point5.exe'

# Resolve any dependent assembly from DEP_DIR (reflection-only)
def _resolver(sender, args):
    name = args.Name.split(',')[0]
    candidate = os.path.join(DEP_DIR, name + '.dll')
    if os.path.exists(candidate):
        try:
            return Assembly.ReflectionOnlyLoadFrom(candidate)
        except Exception:
            pass
    candidate = os.path.join(DEP_DIR, name + '.exe')
    if os.path.exists(candidate):
        try:
            return Assembly.ReflectionOnlyLoadFrom(candidate)
        except Exception:
            pass
    return None

AppDomain.CurrentDomain.ReflectionOnlyAssemblyResolve += ResolveEventHandler(_resolver)
AppDomain.CurrentDomain.AssemblyResolve += ResolveEventHandler(_resolver)

# Pre-load common .NET reference assemblies in reflection-only mode so we
# can resolve System.Drawing, System.Windows.Forms, etc. on demand.
fx_dir = r'C:\Windows\Microsoft.NET\Framework\v4.0.30319'
for fx_dll in ['mscorlib.dll', 'System.dll', 'System.Drawing.dll',
               'System.Windows.Forms.dll', 'System.Core.dll', 'System.Xml.dll']:
    p = os.path.join(fx_dir, fx_dll)
    if os.path.exists(p):
        try:
            Assembly.ReflectionOnlyLoadFrom(p)
        except Exception as e:
            print(f"  preload {fx_dll}: {e}")

asm = Assembly.ReflectionOnlyLoadFrom(PATH)
print(f"Loaded {asm.FullName}")
print(f"Modules: {[m.Name for m in asm.GetModules()]}")

# Walk every type — tolerate ReflectionTypeLoadException by enumerating
# the partial result it provides.
from System.Reflection import ReflectionTypeLoadException
print("\n=== All types ===")
try:
    types = list(asm.GetTypes())
except ReflectionTypeLoadException as ex:
    print(f"  partial type load — using {ex.Types.Length} resolvable types")
    types = [t for t in ex.Types if t is not None]
for t in types:
    if t.FullName and any(k in t.FullName.lower() for k in ['enc', 'crypt', 'aes', 'rsa', 'cipher', 'tnc', 'fw', 'firmware', 'joro', 'talia', 'device']):
        print(f"\n{t.FullName}")
        # List methods (tolerate param-type resolution failures)
        for m in t.GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly):
            try:
                params = ', '.join(p.ParameterType.Name for p in m.GetParameters())
            except Exception:
                params = '?'
            print(f"  method: {m.Name}({params})")
        # List fields (often holds keys / constants)
        for f in t.GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly):
            try:
                if f.IsLiteral or f.IsStatic:
                    val = f.GetValue(None)
                    val_str = repr(val)[:60]
                    print(f"  field: {f.FieldType.Name} {f.Name} = {val_str}")
                else:
                    print(f"  field: {f.FieldType.Name} {f.Name}")
            except Exception as e:
                print(f"  field: {f.FieldType.Name} {f.Name} (val err: {e})")
