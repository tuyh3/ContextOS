package javaindexer;

import org.eclipse.jdt.core.dom.*;

import java.util.*;

/**
 * ASTVisitor that extracts classes, methods, fields, calls, and inheritance
 * from a single CompilationUnit. Generic — no framework-specific logic.
 */
public class SymbolEmitter extends ASTVisitor {

    private final String module;
    private final String filePath;
    private final CompilationUnit cu;
    private final IndexResult result;

    // Stack of enclosing class FQNs (handles inner classes)
    private final Deque<String> classStack = new ArrayDeque<>();
    // Stack of enclosing method FQNs (handles anonymous inner methods)
    private final Deque<String> methodStack = new ArrayDeque<>();

    public SymbolEmitter(String module, String filePath, CompilationUnit cu, IndexResult result) {
        this.module = module;
        this.filePath = filePath;
        this.cu = cu;
        this.result = result;

        // Record file
        String pkg = cu.getPackage() != null ? cu.getPackage().getName().getFullyQualifiedName() : "";
        result.files.add(new IndexResult.FileRecord(filePath, module, pkg));
    }

    // ── Class / Interface / Enum ──

    @Override
    public boolean visit(TypeDeclaration node) {
        return visitType(node, node.isInterface() ? "interface" : "class",
                node.getSuperclassType(), node.superInterfaceTypes(),
                node.modifiers(), node.getName());
    }

    @Override
    public boolean visit(EnumDeclaration node) {
        return visitType(node, "enum", null, node.superInterfaceTypes(),
                node.modifiers(), node.getName());
    }

    @Override
    public boolean visit(AnnotationTypeDeclaration node) {
        return visitType(node, "annotation", null, Collections.emptyList(),
                node.modifiers(), node.getName());
    }

    @SuppressWarnings("unchecked")
    private boolean visitType(AbstractTypeDeclaration node, String kind,
                              Type superclassType, List<?> superInterfaceTypes,
                              List<?> modifierList, SimpleName nameNode) {
        ITypeBinding binding = node.resolveBinding();
        String fqn = binding != null ? binding.getQualifiedName() : buildFqnFromContext(nameNode.getIdentifier());
        String className = nameNode.getIdentifier();
        String pkg = cu.getPackage() != null ? cu.getPackage().getName().getFullyQualifiedName() : "";

        IndexResult.ClassRecord rec = new IndexResult.ClassRecord();
        rec.classId = result.nextClassId();
        rec.classFqn = fqn;
        rec.className = className;
        rec.packageName = pkg;
        rec.filePath = filePath;
        rec.kind = kind;
        rec.startLine = cu.getLineNumber(node.getStartPosition());
        rec.endLine = cu.getLineNumber(node.getStartPosition() + node.getLength() - 1);

        // Superclass
        rec.superclass = "";
        if (superclassType != null) {
            ITypeBinding superBinding = superclassType.resolveBinding();
            rec.superclass = superBinding != null ? superBinding.getQualifiedName() : superclassType.toString();
            result.inheritance.add(new IndexResult.InheritanceRecord(fqn, rec.superclass, "extends"));
            emitRef(rec.superclass, "class", "extends", superclassType);
        }

        // Interfaces
        rec.interfaces = new ArrayList<>();
        for (Object iface : superInterfaceTypes) {
            Type ifaceType = (Type) iface;
            ITypeBinding ifaceBinding = ifaceType.resolveBinding();
            String ifaceFqn = ifaceBinding != null ? ifaceBinding.getQualifiedName() : ifaceType.toString();
            rec.interfaces.add(ifaceFqn);
            result.inheritance.add(new IndexResult.InheritanceRecord(fqn, ifaceFqn, "implements"));
            emitRef(ifaceFqn, "interface", "implements", ifaceType);
        }

        rec.modifiers = extractModifiers(modifierList);
        rec.annotations = extractAnnotations(modifierList);

        result.classes.add(rec);
        classStack.push(fqn);
        return true; // visit children
    }

    @Override
    public void endVisit(TypeDeclaration node) { if (!classStack.isEmpty()) classStack.pop(); }
    @Override
    public void endVisit(EnumDeclaration node) { if (!classStack.isEmpty()) classStack.pop(); }
    @Override
    public void endVisit(AnnotationTypeDeclaration node) { if (!classStack.isEmpty()) classStack.pop(); }

    // ── Methods ──

    @SuppressWarnings("unchecked")
    @Override
    public boolean visit(MethodDeclaration node) {
        String classFqn = classStack.isEmpty() ? "?" : classStack.peek();
        IMethodBinding binding = node.resolveBinding();

        String methodName = node.getName().getIdentifier();
        boolean isCtor = node.isConstructor();

        // Parameter types and names
        List<String> paramTypes = new ArrayList<>();
        List<String> paramNames = new ArrayList<>();
        for (Object param : node.parameters()) {
            SingleVariableDeclaration svd = (SingleVariableDeclaration) param;
            ITypeBinding paramBinding = svd.getType().resolveBinding();
            paramTypes.add(paramBinding != null ? paramBinding.getQualifiedName() : svd.getType().toString());
            paramNames.add(svd.getName().getIdentifier());
        }

        // Signature: methodName(Type1, Type2)
        StringBuilder sig = new StringBuilder(methodName).append("(");
        for (int i = 0; i < paramTypes.size(); i++) {
            if (i > 0) sig.append(", ");
            sig.append(simpleName(paramTypes.get(i)));
        }
        sig.append(")");

        // FQN: com.xx.Service#methodName(Type1,Type2)
        StringBuilder fqnBuilder = new StringBuilder(classFqn).append("#").append(methodName).append("(");
        for (int i = 0; i < paramTypes.size(); i++) {
            if (i > 0) fqnBuilder.append(",");
            fqnBuilder.append(paramTypes.get(i));
        }
        fqnBuilder.append(")");
        String methodFqn = fqnBuilder.toString();

        // Return type
        String returnType = "";
        if (!isCtor && node.getReturnType2() != null) {
            ITypeBinding retBinding = node.getReturnType2().resolveBinding();
            returnType = retBinding != null ? retBinding.getQualifiedName() : node.getReturnType2().toString();
        }

        IndexResult.MethodRecord rec = new IndexResult.MethodRecord();
        rec.methodId = result.nextMethodId();
        rec.classFqn = classFqn;
        rec.methodName = methodName;
        rec.signature = sig.toString();
        rec.methodFqn = methodFqn;
        rec.returnType = returnType;
        rec.paramTypes = paramTypes;
        rec.paramNames = paramNames;
        rec.modifiers = extractModifiers(node.modifiers());
        rec.annotations = extractAnnotations(node.modifiers());
        rec.isConstructor = isCtor;
        rec.startLine = cu.getLineNumber(node.getStartPosition());
        rec.endLine = cu.getLineNumber(node.getStartPosition() + node.getLength() - 1);

        result.methods.add(rec);
        methodStack.push(methodFqn);
        return true;
    }

    @Override
    public void endVisit(MethodDeclaration node) { if (!methodStack.isEmpty()) methodStack.pop(); }

    // ── Fields ──

    @SuppressWarnings("unchecked")
    @Override
    public boolean visit(FieldDeclaration node) {
        String classFqn = classStack.isEmpty() ? "?" : classStack.peek();
        ITypeBinding typeBinding = node.getType().resolveBinding();
        String fieldType = typeBinding != null ? typeBinding.getQualifiedName() : node.getType().toString();

        List<String> modifiers = extractModifiers(node.modifiers());
        List<String> annotations = extractAnnotations(node.modifiers());
        int startLine = cu.getLineNumber(node.getStartPosition());
        int endLine = cu.getLineNumber(node.getStartPosition() + node.getLength() - 1);

        // One FieldDeclaration can declare multiple variables: int a, b;
        for (Object frag : node.fragments()) {
            VariableDeclarationFragment vdf = (VariableDeclarationFragment) frag;
            IndexResult.FieldRecord rec = new IndexResult.FieldRecord();
            rec.fieldId = result.nextFieldId();
            rec.classFqn = classFqn;
            rec.fieldName = vdf.getName().getIdentifier();
            rec.fieldType = fieldType;
            rec.modifiers = modifiers;
            rec.annotations = annotations;
            rec.startLine = startLine;
            rec.endLine = endLine;
            result.fields.add(rec);
        }
        return true;
    }

    // ── Method calls ──

    @Override
    public boolean visit(MethodInvocation node) {
        if (methodStack.isEmpty()) return true; // call outside method (e.g., field initializer)
        String callerFqn = methodStack.peek();

        IMethodBinding binding = node.resolveMethodBinding();
        String calleeClassName = "";
        String calleeMethodName = node.getName().getIdentifier();
        String calleeMethodFqn = "";
        String calleeSig = "";
        String receiverType = "";
        String dispatchKind = "unknown";
        boolean resolved = false;

        if (binding != null) {
            resolved = true;
            ITypeBinding declaringClass = binding.getDeclaringClass();
            if (declaringClass != null) {
                calleeClassName = declaringClass.getQualifiedName();
            }
            calleeSig = buildSignature(binding);
            calleeMethodFqn = calleeClassName + "#" + calleeSig;

            // Determine dispatch kind
            if (Modifier.isStatic(binding.getModifiers())) {
                dispatchKind = "static";
            }
        }

        // Determine dispatch kind from receiver expression
        Expression receiver = node.getExpression();
        if (receiver == null) {
            // Implicit this or static import
            if (!"static".equals(dispatchKind)) dispatchKind = "this";
        } else {
            ITypeBinding recvBinding = receiver.resolveTypeBinding();
            if (recvBinding != null) {
                receiverType = recvBinding.getQualifiedName();
            }
            if (receiver instanceof ThisExpression) {
                dispatchKind = "this";
            } else if (receiver instanceof FieldAccess || receiver instanceof SimpleName) {
                if (!"static".equals(dispatchKind)) dispatchKind = "field";
            }
        }

        emitCall(callerFqn, calleeClassName, calleeMethodName, calleeSig,
                calleeMethodFqn, receiverType, dispatchKind,
                cu.getLineNumber(node.getStartPosition()), resolved);
        // Also emit reference
        if (!calleeMethodFqn.isEmpty()) {
            emitRef(calleeMethodFqn, "method", "method_call", node);
        } else {
            String target = calleeClassName.isEmpty() ? calleeMethodName : calleeClassName + "#" + calleeMethodName;
            emitRef(target, "unresolved", "method_call", node);
        }
        return true;
    }

    @Override
    public boolean visit(SuperMethodInvocation node) {
        if (methodStack.isEmpty()) return true;
        String callerFqn = methodStack.peek();

        IMethodBinding binding = node.resolveMethodBinding();
        String calleeClassName = "";
        String calleeMethodName = node.getName().getIdentifier();
        String calleeSig = "";
        String calleeMethodFqn = "";
        boolean resolved = false;

        if (binding != null) {
            resolved = true;
            ITypeBinding declaringClass = binding.getDeclaringClass();
            if (declaringClass != null) calleeClassName = declaringClass.getQualifiedName();
            calleeSig = buildSignature(binding);
            calleeMethodFqn = calleeClassName + "#" + calleeSig;
        }

        emitCall(callerFqn, calleeClassName, calleeMethodName, calleeSig,
                calleeMethodFqn, "", "super",
                cu.getLineNumber(node.getStartPosition()), resolved);
        if (!calleeMethodFqn.isEmpty()) {
            emitRef(calleeMethodFqn, "method", "method_call", node);
        } else {
            String target = calleeClassName.isEmpty() ? calleeMethodName : calleeClassName + "#" + calleeMethodName;
            emitRef(target, "unresolved", "method_call", node);
        }
        return true;
    }

    @Override
    public boolean visit(ClassInstanceCreation node) {
        if (methodStack.isEmpty()) return true;
        String callerFqn = methodStack.peek();

        IMethodBinding binding = node.resolveConstructorBinding();
        String calleeClassName = "";
        String calleeSig = "";
        String calleeMethodFqn = "";
        boolean resolved = false;

        if (binding != null) {
            resolved = true;
            ITypeBinding declaringClass = binding.getDeclaringClass();
            if (declaringClass != null) calleeClassName = declaringClass.getQualifiedName();
            calleeSig = buildSignature(binding);
            calleeMethodFqn = calleeClassName + "#" + calleeSig;
        } else {
            // Fallback: get type name from AST
            Type type = node.getType();
            ITypeBinding typeBinding = type.resolveBinding();
            calleeClassName = typeBinding != null ? typeBinding.getQualifiedName() : type.toString();
        }

        emitCall(callerFqn, calleeClassName, "<init>", calleeSig,
                calleeMethodFqn, calleeClassName, "new",
                cu.getLineNumber(node.getStartPosition()), resolved);
        if (!calleeMethodFqn.isEmpty()) {
            emitRef(calleeMethodFqn, "method", "method_call", node);
        } else {
            emitRef(calleeClassName.isEmpty() ? "<init>" : calleeClassName + "#<init>",
                    "unresolved", "method_call", node);
        }
        return true;
    }

    // ── References (type_use / field_access / import / annotation) ──

    @Override
    public boolean visit(SimpleType node) {
        if (isExtendsOrImplementsContext(node)) return true; // handled in visitType
        ITypeBinding binding = node.resolveBinding();
        if (binding != null) {
            emitRef(binding.getQualifiedName(), typeKind(binding), "type_use", node);
        } else {
            emitRef(node.getName().getFullyQualifiedName(), "unresolved", "type_use", node);
        }
        return true;
    }

    @Override
    public boolean visit(QualifiedType node) {
        if (isExtendsOrImplementsContext(node)) return true;
        ITypeBinding binding = node.resolveBinding();
        if (binding != null) {
            emitRef(binding.getQualifiedName(), typeKind(binding), "type_use", node);
        } else {
            emitRef(node.getName().getIdentifier(), "unresolved", "type_use", node);
        }
        return true;
    }

    @Override
    public boolean visit(FieldAccess node) {
        IVariableBinding binding = node.resolveFieldBinding();
        if (binding != null && binding.isField()) {
            ITypeBinding declClass = binding.getDeclaringClass();
            String classFqn = declClass != null ? declClass.getQualifiedName() : "";
            emitRef(classFqn + "#" + binding.getName(), "field", "field_access", node);
        }
        return true;
    }

    @Override
    public boolean visit(QualifiedName node) {
        IBinding binding = node.resolveBinding();
        if (binding instanceof IVariableBinding) {
            IVariableBinding varBinding = (IVariableBinding) binding;
            if (varBinding.isField()) {
                ITypeBinding declClass = varBinding.getDeclaringClass();
                String classFqn = declClass != null ? declClass.getQualifiedName() : "";
                emitRef(classFqn + "#" + varBinding.getName(), "field", "field_access", node);
            }
        }
        return true;
    }

    @Override
    public boolean visit(ImportDeclaration node) {
        IBinding binding = node.resolveBinding();
        if (binding instanceof ITypeBinding) {
            ITypeBinding typeBinding = (ITypeBinding) binding;
            emitRef(typeBinding.getQualifiedName(), typeKind(typeBinding), "import", node);
        } else if (binding instanceof IMethodBinding) {
            IMethodBinding methodBinding = (IMethodBinding) binding;
            String target = "";
            if (methodBinding.getDeclaringClass() != null) {
                target = methodBinding.getDeclaringClass().getQualifiedName() + "#" + buildSignature(methodBinding);
            }
            emitRef(target, "method", "import", node);
        } else if (binding instanceof IVariableBinding) {
            IVariableBinding varBinding = (IVariableBinding) binding;
            String classFqn = varBinding.getDeclaringClass() != null ? varBinding.getDeclaringClass().getQualifiedName() : "";
            emitRef(classFqn + "#" + varBinding.getName(), "field", "import", node);
        } else {
            emitRef(node.getName().getFullyQualifiedName(), "unresolved", "import", node);
        }
        return false; // don't visit children of import
    }

    @Override
    public boolean visit(MarkerAnnotation node) { return visitAnnotation(node); }
    @Override
    public boolean visit(NormalAnnotation node) { return visitAnnotation(node); }
    @Override
    public boolean visit(SingleMemberAnnotation node) { return visitAnnotation(node); }

    private boolean visitAnnotation(Annotation node) {
        ITypeBinding binding = node.resolveTypeBinding();
        if (binding != null) {
            emitRef(binding.getQualifiedName(), typeKind(binding), "annotation", node);
        } else {
            emitRef(node.getTypeName().getFullyQualifiedName(), "unresolved", "annotation", node);
        }
        return true;
    }

    // ── Helpers ──

    private void emitRef(String targetFqn, String targetKind, String refKind, ASTNode node) {
        IndexResult.ReferenceRecord rec = new IndexResult.ReferenceRecord();
        rec.sourceFqn = currentSourceFqn();
        rec.sourceFile = filePath;
        rec.targetFqn = targetFqn;
        rec.targetKind = targetKind;
        rec.refKind = refKind;
        rec.lineNo = cu.getLineNumber(node.getStartPosition());
        rec.columnNo = cu.getColumnNumber(node.getStartPosition());
        result.references.add(rec);
    }

    private String currentSourceFqn() {
        if (!methodStack.isEmpty()) return methodStack.peek();
        if (!classStack.isEmpty()) return classStack.peek();
        String pkg = cu.getPackage() != null ? cu.getPackage().getName().getFullyQualifiedName() : "";
        return pkg.isEmpty() ? filePath : pkg + ".*";
    }

    private String typeKind(ITypeBinding binding) {
        if (binding.isInterface()) return "interface"; // includes annotation types
        if (binding.isEnum()) return "enum";
        return "class";
    }

    private boolean isExtendsOrImplementsContext(Type node) {
        StructuralPropertyDescriptor loc = node.getLocationInParent();
        if (loc == TypeDeclaration.SUPERCLASS_TYPE_PROPERTY
                || loc == TypeDeclaration.SUPER_INTERFACE_TYPES_PROPERTY
                || loc == EnumDeclaration.SUPER_INTERFACE_TYPES_PROPERTY) {
            return true;
        }
        // Check if this is the base type of a ParameterizedType in extends/implements
        if (node.getParent() instanceof ParameterizedType) {
            ParameterizedType pt = (ParameterizedType) node.getParent();
            if (pt.getType() == node) {
                return isExtendsOrImplementsContext(pt);
            }
        }
        return false;
    }

    private void emitCall(String callerFqn, String calleeClassName, String calleeMethodName,
                          String calleeSig, String calleeMethodFqn, String receiverType,
                          String dispatchKind, int lineNo, boolean resolved) {
        IndexResult.CallRecord rec = new IndexResult.CallRecord();
        rec.callId = result.nextCallId();
        rec.callerMethodFqn = callerFqn;
        rec.calleeClassFqn = calleeClassName;
        rec.calleeMethodName = calleeMethodName;
        rec.calleeSignature = calleeSig;
        rec.calleeMethodFqn = calleeMethodFqn;
        rec.receiverType = receiverType;
        rec.dispatchKind = dispatchKind;
        rec.lineNo = lineNo;
        rec.resolved = resolved;
        result.calls.add(rec);
    }

    private String buildSignature(IMethodBinding binding) {
        StringBuilder sb = new StringBuilder(binding.getName()).append("(");
        ITypeBinding[] params = binding.getParameterTypes();
        for (int i = 0; i < params.length; i++) {
            if (i > 0) sb.append(",");
            sb.append(params[i].getQualifiedName());
        }
        sb.append(")");
        return sb.toString();
    }

    private String buildFqnFromContext(String simpleName) {
        String pkg = cu.getPackage() != null ? cu.getPackage().getName().getFullyQualifiedName() : "";
        if (!classStack.isEmpty()) {
            return classStack.peek() + "." + simpleName; // inner class
        }
        return pkg.isEmpty() ? simpleName : pkg + "." + simpleName;
    }

    @SuppressWarnings("unchecked")
    private List<String> extractModifiers(List<?> modifiers) {
        List<String> result = new ArrayList<>();
        for (Object mod : modifiers) {
            if (mod instanceof Modifier) {
                result.add(((Modifier) mod).getKeyword().toString());
            }
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    private List<String> extractAnnotations(List<?> modifiers) {
        List<String> result = new ArrayList<>();
        for (Object mod : modifiers) {
            if (mod instanceof Annotation) {
                result.add("@" + ((Annotation) mod).getTypeName().getFullyQualifiedName());
            }
        }
        return result;
    }

    private String simpleName(String fqn) {
        int dot = fqn.lastIndexOf('.');
        return dot >= 0 ? fqn.substring(dot + 1) : fqn;
    }
}
