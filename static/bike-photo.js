// One small JPEG per bike. Kept outside listing documents and excluded from indexes.
export const MAX_PHOTO_LENGTH = 220000;
export function validPhoto(value) {
  return (
    typeof value === "string" &&
    value.length <= MAX_PHOTO_LENGTH &&
    /^data:image\/jpeg;base64,[A-Za-z0-9+/]+={0,2}$/.test(value)
  );
}
export async function preparePhoto(file) {
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    throw new Error(
      "Choose a JPEG, PNG, or WebP photo. Export HEIC photos as JPEG first.",
    );
  }
  if (file.size > 15 * 1024 * 1024)
    throw new Error("Choose a photo smaller than 15 MB.");
  let bitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new Error(
      "This photo could not be opened. Try another JPEG, PNG, or WebP.",
    );
  }
  try {
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    for (const size of [1200, 960, 720, 480]) {
      const scale = Math.min(1, size / Math.max(bitmap.width, bitmap.height));
      canvas.width = Math.max(1, Math.round(bitmap.width * scale));
      canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      for (const quality of [0.82, 0.65, 0.45]) {
        // Canvas re-encoding discards original EXIF metadata, including GPS.
        const result = canvas.toDataURL("image/jpeg", quality);
        if (validPhoto(result)) return result;
      }
    }
    throw new Error(
      "This photo could not be made small enough. Try a simpler or cropped image.",
    );
  } finally {
    bitmap.close();
  }
}
