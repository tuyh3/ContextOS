package javaindexer;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.File;

/**
 * CLI entry point for the Java Indexer.
 *
 * Usage:
 *   java -jar java-indexer.jar <build_context.json> <output_dir> [--files <list.txt>]
 *
 * Input:  build_context.json — module definitions with source roots and classpath
 *         optional --files list.txt — absolute paths of .java files to parse
 *         (subset mode for incremental updates; environment still spans all
 *         source roots + classpath, so cross-file bindings resolve normally)
 * Output: JSONL files in output_dir — files/classes/methods/fields/calls/inheritance
 */
public class Main {

    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("Usage: java -jar java-indexer.jar <build_context.json> <output_dir> [--files <list.txt>]");
            System.err.println();
            System.err.println("  build_context.json  Module definitions (source roots, classpath)");
            System.err.println("  output_dir          Directory for JSONL output files");
            System.err.println("  --files list.txt    Optional: only parse these .java files (absolute paths, one per line)");
            System.exit(1);
        }

        String contextPath = args[0];
        String outputDir = args[1];

        java.util.Set<String> onlyFiles = null;
        for (int i = 2; i < args.length - 1; i++) {
            if ("--files".equals(args[i])) {
                onlyFiles = new java.util.HashSet<>(
                        java.nio.file.Files.readAllLines(java.nio.file.Paths.get(args[i + 1])));
                onlyFiles.removeIf(String::isEmpty);
            }
        }

        ObjectMapper mapper = new ObjectMapper();
        BuildContext ctx = mapper.readValue(new File(contextPath), BuildContext.class);

        System.out.printf("Java Indexer: %d modules, Java %s%n", ctx.modules.size(), ctx.javaVersion);
        if (onlyFiles != null) {
            System.out.printf("Subset mode: %d files from --files list%n", onlyFiles.size());
        }

        JdtIndexer indexer = new JdtIndexer(ctx, onlyFiles);
        IndexResult result = indexer.run();

        System.out.println("Writing output:");
        JsonWriter.write(result, outputDir, mapper);

        System.out.printf("%nSummary: %,d files, %,d classes, %,d methods, %,d fields, %,d calls, %,d inheritance, %,d references%n",
                result.files.size(), result.classes.size(), result.methods.size(),
                result.fields.size(), result.calls.size(), result.inheritance.size(),
                result.references.size());
    }
}
