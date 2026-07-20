package net.server.transformer;

import org.objectweb.asm.ClassReader;
import org.objectweb.asm.ClassWriter;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.logging.Level;
import java.util.logging.Logger;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import java.util.zip.ZipOutputStream;

public class ModTransformer {
    private static final Logger LOGGER = Logger.getLogger("MOD-TRANSFORMER");
    private final MetadataTransformer metadataTransformer;

    public ModTransformer(String targetVersion) {
        this.metadataTransformer = new MetadataTransformer(targetVersion);
    }

    public void transformJar(Path sourceJar, Path destJar) throws IOException {
        long startTime = System.currentTimeMillis();
        List<String> transformedClasses = new ArrayList<>();
        
        try (ZipInputStream zis = new ZipInputStream(new BufferedInputStream(new FileInputStream(sourceJar.toFile())));
             ZipOutputStream zos = new ZipOutputStream(new BufferedOutputStream(new FileOutputStream(destJar.toFile())))) {
            
            ZipEntry entry;
            while ((entry = zis.getNextEntry()) != null) {
                String name = entry.getName();
                byte[] rawData = readEntryBytes(zis);
                byte[] processedData = rawData;

                if (name.endsWith(".class")) {
                    try {
                        ClassReader reader = new ClassReader(rawData);
                        ClassWriter writer = new ClassWriter(reader, ClassWriter.COMPUTE_MAXS);
                        SecurityClassVisitor cv = new SecurityClassVisitor(writer);
                        
                        reader.accept(cv, 0);
                        
                        if (cv.isModified()) {
                            processedData = writer.toByteArray();
                            transformedClasses.add(name);
                        }
                    } catch (Exception e) {
                        LOGGER.log(Level.SEVERE, "Failed parsing bytecode for: " + name + ". Passing raw copy.", e);
                    }
                } else if (isMetadataFile(name)) {
                    String decoded = new String(rawData, StandardCharsets.UTF_8);
                    String updated = metadataTransformer.rewriteMetadata(name, decoded);
                    processedData = updated.getBytes(StandardCharsets.UTF_8);
                }

                ZipEntry newEntry = new ZipEntry(name);
                zos.putNextEntry(newEntry);
                zos.write(processedData);
                zos.closeEntry();
                zis.closeEntry();
            }
        }

        LOGGER.info(() -> String.format("Transformation completed in %dms. Modified %d classes.", 
            (System.currentTimeMillis() - startTime), transformedClasses.size()));
    }

    private boolean isMetadataFile(String name) {
        return name.endsWith("mcmod.info") || name.endsWith("mods.toml") || 
               name.endsWith("neoforge.mods.toml") || name.endsWith("fabric.mod.json") || 
               name.endsWith("pack.mcmeta") || name.endsWith(".refmap.json");
    }

    /**
     * ZipInputStream#readAllBytes was added in Java 9.  The porter targets
     * Java 8+ so read the current entry explicitly instead.
     */
    private static byte[] readEntryBytes(InputStream input) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[8 * 1024];
        int read;
        while ((read = input.read(buffer)) != -1) {
            output.write(buffer, 0, read);
        }
        return output.toByteArray();
    }
}
