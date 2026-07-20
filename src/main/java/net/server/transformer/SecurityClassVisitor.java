package net.server.transformer;

import org.objectweb.asm.ClassVisitor;
import org.objectweb.asm.MethodVisitor;
import org.objectweb.asm.Opcodes;

public class SecurityClassVisitor extends ClassVisitor {
    private boolean modified = false;

    public SecurityClassVisitor(ClassVisitor classVisitor) {
        super(Opcodes.ASM9, classVisitor);
    }

    public boolean isModified() {
        return modified;
    }

    @Override
    public MethodVisitor visitMethod(int access, String name, String descriptor, String signature, String[] exceptions) {
        MethodVisitor mv = super.visitMethod(access, name, descriptor, signature, exceptions);
        return new MethodVisitor(Opcodes.ASM9, mv) {
            
            @Override
            public void visitMethodInsn(int opcode, String owner, String methodName, String methodDesc, boolean isInterface) {
                // Mitigation 1: Catch main-thread blocking chunk loading and route to async handler
                if (methodName.equals("getChunkFromChunkCoords") || methodName.equals("generateTree")) {
                    super.visitMethodInsn(
                        Opcodes.INVOKESTATIC, 
                        "net/server/bridge/WorldAsyncCache", 
                        "getChunkSafe", 
                        "(Lnet/minecraft/world/level/Level;II)Lnet/minecraft/world/level/chunk/LevelChunk;", 
                        false
                    );
                    modified = true;
                    return;
                }

                // Mitigation 2: Intercept unvalidated NBT inputs to prevent NBT payload crash exploits
                if (methodName.equals("readTag") || methodName.equals("getTagCompound") || methodName.equals("readNBT")) {
                    super.visitMethodInsn(
                        Opcodes.INVOKESTATIC, 
                        "net/server/bridge/DataComponentSanitizer", 
                        "parseUntrusted", 
                        "(Ljava/io/DataInput;)Lnet/minecraft/nbt/CompoundTag;", 
                        false
                    );
                    modified = true;
                    return;
                }

                super.visitMethodInsn(opcode, owner, methodName, methodDesc, isInterface);
            }

            @Override
            public void visitLdcInsn(Object value) {
                // Mitigation 3: Surgical fix for brittle synthetic Mixin lambda shifts (e.g. Flywheel crash)
                if (value instanceof String) {
                    String stringConstant = (String) value;
                    if (stringConstant.equals("lambda$new$8")) {
                        super.visitLdcInsn("lambda$new$10"); // Direct Constant Pool String Mapping Override
                        modified = true;
                        return;
                    }
                }
                super.visitLdcInsn(value);
            }
        };
    }
}
