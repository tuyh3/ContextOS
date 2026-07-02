package javaindexer;

import org.eclipse.jdt.core.dom.AST;
import org.eclipse.jdt.core.dom.ASTParser;
import org.eclipse.jdt.core.dom.CompilationUnit;
import org.eclipse.jdt.core.dom.FileASTRequestor;

import java.io.IOException;
import java.nio.file.*;
import java.util.*;
import java.util.stream.Stream;

/**
 * Batch-parses Java source files using Eclipse JDT ASTParser with binding resolution.
 * Generic — no project-specific logic.
 *
 * Supports two modes:
 * - Per-module: each module parsed independently (fast, lower resolution)
 * - Shared-env: all source roots shared as environment, files parsed in batches
 *   per module (slower, much higher cross-module resolution)
 */
public class JdtIndexer {

    private final BuildContext context;
    private final IndexResult result = new IndexResult();

    /**
     * Optional subset filter (ContextOS --files patch): absolute paths of the
     * only .java files to parse. null = parse everything. The parser environment
     * still spans ALL source roots + classpath, so cross-file bindings resolve.
     */
    private final Set<String> onlyFiles;

    public JdtIndexer(BuildContext context) {
        this(context, null);
    }

    public JdtIndexer(BuildContext context, Set<String> onlyFiles) {
        this.context = context;
        this.onlyFiles = onlyFiles;
    }

    public IndexResult run() {
        // Collect ALL source roots across all modules for shared environment
        Set<String> allSourceRoots = new LinkedHashSet<>();
        Set<String> allClasspath = new LinkedHashSet<>();
        for (BuildContext.Module module : context.modules) {
            allSourceRoots.addAll(module.sourceRoots);
            allClasspath.addAll(module.classpathEntries);
        }

        boolean sharedEnv = context.modules.size() > 1 && allSourceRoots.size() > 1;
        if (sharedEnv) {
            System.out.printf("Shared environment: %d source roots, %d classpath entries%n",
                    allSourceRoots.size(), allClasspath.size());
        }

        for (BuildContext.Module module : context.modules) {
            if (sharedEnv) {
                processModuleWithSharedEnv(module, allSourceRoots, allClasspath);
            } else {
                processModule(module);
            }
        }
        return result;
    }

    /**
     * Parse a module's files using ALL source roots as environment.
     * This gives JDT visibility into all modules for cross-module binding resolution,
     * while only parsing the current module's files (bounded memory).
     */
    private void processModuleWithSharedEnv(BuildContext.Module module,
                                             Set<String> allSourceRoots,
                                             Set<String> allClasspath) {
        // Collect .java files from THIS module's source roots only
        List<String> javaFiles = collectJavaFiles(module);
        if (onlyFiles != null) {
            javaFiles.removeIf(f -> !onlyFiles.contains(f));
        }
        if (javaFiles.isEmpty()) {
            System.err.printf("WARN: no .java files in module '%s', skipping%n", module.name);
            return;
        }

        System.out.printf("[%s] %d files (env: %d source roots, %d classpath)%n",
                module.name, javaFiles.size(), allSourceRoots.size(), allClasspath.size());

        String encoding = module.encoding != null ? module.encoding : "UTF-8";

        // New parser for each module (releases memory from previous batch)
        ASTParser parser = ASTParser.newParser(AST.JLS8);
        parser.setResolveBindings(true);
        parser.setBindingsRecovery(true);
        parser.setStatementsRecovery(true);
        parser.setKind(ASTParser.K_COMPILATION_UNIT);

        // Environment: ALL source roots + classpath → cross-module resolution
        String[] srcArray = allSourceRoots.toArray(new String[0]);
        String[] srcEncodings = new String[srcArray.length];
        Arrays.fill(srcEncodings, encoding);
        String[] cpArray = allClasspath.toArray(new String[0]);

        parser.setEnvironment(cpArray, srcArray, srcEncodings, true);

        // Only parse THIS module's files
        String[] fileArray = javaFiles.toArray(new String[0]);
        String[] fileEncodings = new String[fileArray.length];
        Arrays.fill(fileEncodings, encoding);

        String moduleName = module.name;
        parser.createASTs(fileArray, fileEncodings, new String[0],
                new FileASTRequestor() {
                    @Override
                    public void acceptAST(String sourceFilePath, CompilationUnit cu) {
                        SymbolEmitter emitter = new SymbolEmitter(moduleName, sourceFilePath, cu, result);
                        cu.accept(emitter);
                    }
                }, null);

        System.out.printf("[%s] done — %d classes so far%n", module.name, result.classes.size());
    }

    /**
     * Original per-module parsing (no cross-module resolution).
     */
    private void processModule(BuildContext.Module module) {
        List<String> javaFiles = collectJavaFiles(module);
        if (onlyFiles != null) {
            javaFiles.removeIf(f -> !onlyFiles.contains(f));
        }
        if (javaFiles.isEmpty()) {
            System.err.printf("WARN: no .java files in module '%s', skipping%n", module.name);
            return;
        }

        System.out.printf("[%s] %d files, %d source roots, %d classpath entries%n",
                module.name, javaFiles.size(), module.sourceRoots.size(),
                module.classpathEntries.size());

        String encoding = module.encoding != null ? module.encoding : "UTF-8";

        ASTParser parser = ASTParser.newParser(AST.JLS8);
        parser.setResolveBindings(true);
        parser.setBindingsRecovery(true);
        parser.setStatementsRecovery(true);
        parser.setKind(ASTParser.K_COMPILATION_UNIT);

        String[] cpArray = module.classpathEntries.toArray(new String[0]);
        String[] srcArray = module.sourceRoots.toArray(new String[0]);
        String[] srcEncodings = new String[srcArray.length];
        Arrays.fill(srcEncodings, encoding);

        parser.setEnvironment(cpArray, srcArray, srcEncodings, true);

        String[] fileArray = javaFiles.toArray(new String[0]);
        String[] fileEncodings = new String[fileArray.length];
        Arrays.fill(fileEncodings, encoding);

        String moduleName = module.name;
        parser.createASTs(fileArray, fileEncodings, new String[0],
                new FileASTRequestor() {
                    @Override
                    public void acceptAST(String sourceFilePath, CompilationUnit cu) {
                        SymbolEmitter emitter = new SymbolEmitter(moduleName, sourceFilePath, cu, result);
                        cu.accept(emitter);
                    }
                }, null);

        System.out.printf("[%s] done — %d classes so far%n", module.name, result.classes.size());
    }

    private List<String> collectJavaFiles(BuildContext.Module module) {
        List<String> javaFiles = new ArrayList<>();
        for (String root : module.sourceRoots) {
            Path rootPath = Paths.get(root);
            if (!Files.isDirectory(rootPath)) {
                System.err.printf("WARN: source root does not exist: %s%n", root);
                continue;
            }
            try (Stream<Path> walk = Files.walk(rootPath)) {
                walk.filter(p -> p.toString().endsWith(".java"))
                    .forEach(p -> javaFiles.add(p.toString()));
            } catch (IOException e) {
                System.err.printf("WARN: cannot walk source root %s: %s%n", root, e.getMessage());
            }
        }
        return javaFiles;
    }
}
