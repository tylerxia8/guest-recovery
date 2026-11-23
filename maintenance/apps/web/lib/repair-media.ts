const formats: Readonly<Record<string, string>> = {
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  jfif: "image/jpeg",
  png: "image/png",
  heic: "image/heic",
  heif: "image/heif",
  webp: "image/webp",
  gif: "image/gif",
  bmp: "image/bmp",
  tif: "image/tiff",
  tiff: "image/tiff",
  avif: "image/avif",
  mp4: "video/mp4",
  mov: "video/quicktime",
};

const supportedTypes = new Set(Object.values(formats));

export const repairMediaAccept = Object.entries(formats)
  .map(([extension]) => `.${extension}`)
  .concat([...supportedTypes])
  .join(",");

export const repairPhotoAccept = repairMediaAccept.split(",")
  .filter(value => ![".mp4", ".mov", "video/mp4", "video/quicktime"].includes(value))
  .join(",");

export function repairMediaType(file: File): string | undefined {
  const mime = file.type.toLowerCase();
  if (mime === "image/jpg" || mime === "image/pjpeg") return "image/jpeg";
  if (supportedTypes.has(mime)) return mime;
  const extension = /\.([a-z0-9]+)$/i.exec(file.name)?.[1].toLowerCase();
  return extension && Object.hasOwn(formats, extension) ? formats[extension] : undefined;
}

export function validRepairMedia(file: File, photosOnly = false): boolean {
  const type = repairMediaType(file);
  if (!type || file.size === 0) return false;
  const video = type.startsWith("video/");
  return !(photosOnly && video) && file.size <= (video ? 200 : 20) * 1024 * 1024;
}
