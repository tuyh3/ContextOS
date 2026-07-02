package javaindexer;

import java.util.*;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Thread-safe accumulator for all extracted symbols.
 * Each record type maps 1:1 to a SQLite table in the downstream emit_sqlite.py.
 */
public class IndexResult {

    // Counters for generating unique IDs
    private final AtomicInteger classSeq = new AtomicInteger(0);
    private final AtomicInteger methodSeq = new AtomicInteger(0);
    private final AtomicInteger fieldSeq = new AtomicInteger(0);
    private final AtomicInteger callSeq = new AtomicInteger(0);

    // ConcurrentLinkedQueue: FileASTRequestor callbacks may run on worker threads
    public final ConcurrentLinkedQueue<FileRecord> files = new ConcurrentLinkedQueue<>();
    public final ConcurrentLinkedQueue<ClassRecord> classes = new ConcurrentLinkedQueue<>();
    public final ConcurrentLinkedQueue<MethodRecord> methods = new ConcurrentLinkedQueue<>();
    public final ConcurrentLinkedQueue<FieldRecord> fields = new ConcurrentLinkedQueue<>();
    public final ConcurrentLinkedQueue<CallRecord> calls = new ConcurrentLinkedQueue<>();
    public final ConcurrentLinkedQueue<InheritanceRecord> inheritance = new ConcurrentLinkedQueue<>();
    public final ConcurrentLinkedQueue<ReferenceRecord> references = new ConcurrentLinkedQueue<>();

    public String nextClassId()  { return "C" + classSeq.incrementAndGet(); }
    public String nextMethodId() { return "M" + methodSeq.incrementAndGet(); }
    public String nextFieldId()  { return "F" + fieldSeq.incrementAndGet(); }
    public String nextCallId()   { return "X" + callSeq.incrementAndGet(); }

    // ── Record types ──

    public static class FileRecord {
        public String path;
        public String module;
        public String packageName;

        public FileRecord(String path, String module, String packageName) {
            this.path = path;
            this.module = module;
            this.packageName = packageName;
        }
    }

    public static class ClassRecord {
        public String classId;
        public String classFqn;
        public String className;
        public String packageName;
        public String filePath;
        public String kind;          // class / interface / enum / annotation
        public String superclass;
        public List<String> interfaces;
        public List<String> modifiers;
        public List<String> annotations;
        public int startLine;
        public int endLine;
    }

    public static class MethodRecord {
        public String methodId;
        public String classFqn;
        public String methodName;
        public String signature;      // createOrder(String, Long)
        public String methodFqn;      // com.xx.OrderService#createOrder(String,Long)
        public String returnType;
        public List<String> paramTypes;
        public List<String> paramNames;
        public List<String> modifiers;
        public List<String> annotations;
        public boolean isConstructor;
        public int startLine;
        public int endLine;
    }

    public static class FieldRecord {
        public String fieldId;
        public String classFqn;
        public String fieldName;
        public String fieldType;
        public List<String> modifiers;
        public List<String> annotations;
        public int startLine;
        public int endLine;
    }

    public static class CallRecord {
        public String callId;
        public String callerMethodFqn;
        public String calleeClassFqn;
        public String calleeMethodName;
        public String calleeSignature;
        public String calleeMethodFqn;
        public String receiverType;
        public String dispatchKind;   // this / field / local / static / unknown
        public int lineNo;
        public boolean resolved;
    }

    public static class InheritanceRecord {
        public String subClassFqn;
        public String superClassFqn;
        public String relationType;   // extends / implements

        public InheritanceRecord(String sub, String sup, String rel) {
            this.subClassFqn = sub;
            this.superClassFqn = sup;
            this.relationType = rel;
        }
    }

    public static class ReferenceRecord {
        public String sourceFqn;     // method FQN or class FQN where reference occurs
        public String sourceFile;    // source file path (relative to repo root)
        public String targetFqn;     // referenced symbol FQN (simple name if unresolved)
        public String targetKind;    // class/interface/enum/method/field/unresolved
        public String refKind;       // type_use/field_access/method_call/import/annotation/extends/implements
        public int lineNo;
        public int columnNo;
    }
}
