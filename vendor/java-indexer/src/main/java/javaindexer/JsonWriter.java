package javaindexer;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.Collection;

/**
 * Writes IndexResult to JSONL files (one JSON object per line).
 * Output directory structure:
 *   files.jsonl, classes.jsonl, methods.jsonl, fields.jsonl, calls.jsonl, inheritance.jsonl
 */
public class JsonWriter {

    public static void write(IndexResult result, String outputDir, ObjectMapper mapper) throws IOException {
        mapper.disable(SerializationFeature.INDENT_OUTPUT); // compact, one line per record

        Path dir = Paths.get(outputDir);
        Files.createDirectories(dir);

        writeJsonl(dir.resolve("files.jsonl"), result.files, mapper);
        writeJsonl(dir.resolve("classes.jsonl"), result.classes, mapper);
        writeJsonl(dir.resolve("methods.jsonl"), result.methods, mapper);
        writeJsonl(dir.resolve("fields.jsonl"), result.fields, mapper);
        writeJsonl(dir.resolve("calls.jsonl"), result.calls, mapper);
        writeJsonl(dir.resolve("inheritance.jsonl"), result.inheritance, mapper);
        writeJsonl(dir.resolve("references.jsonl"), result.references, mapper);
    }

    private static <T> void writeJsonl(Path path, Collection<T> records, ObjectMapper mapper) throws IOException {
        try (BufferedWriter writer = Files.newBufferedWriter(path, StandardCharsets.UTF_8)) {
            for (T record : records) {
                writer.write(mapper.writeValueAsString(record));
                writer.newLine();
            }
        }
        System.out.printf("  %-20s %,d records%n", path.getFileName(), records.size());
    }
}
