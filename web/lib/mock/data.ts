/**
 * Deterministic fixture data for mock mode. Content is derived from docs/phase0-results.md (node counts and the
 * mostly-low confidence profile of spring-petclinic) and the sample projects in the repo (django-lite).
 */
import type { Confidence, Evidence, Finding, GraphEdge, GraphNode } from "../types";

export function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

export function uuid(seed: string): string {
  let h = 2166136261;
  const out: string[] = [];
  for (let k = 0; k < 4; k++) {
    for (let i = 0; i < seed.length; i++) {
      h ^= seed.charCodeAt(i) + k;
      h = Math.imul(h, 16777619);
    }
    out.push((h >>> 0).toString(16).padStart(8, "0"));
  }
  const x = out.join("");
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-4${x.slice(13, 16)}-a${x.slice(17, 20)}-${x.slice(20, 32)}`;
}

/** Internal bookkeeping for versioned snapshots. */
export interface VNode extends GraphNode {
  since: number;
  until?: number;
  changedIn?: number[];
}
export interface VEdge extends GraphEdge {
  since: number;
  until?: number;
}
export interface FixtureGraph {
  nodes: VNode[];
  edges: VEdge[];
  versions: number[];
}

const PREFIX: Record<string, string> = {
  component: "COMP", endpoint: "EP", data_entity: "ENT", business_rule: "RULE", external_dependency: "EXT", config: "CFG",
};

class Builder {
  nodes: VNode[] = [];
  edges: VEdge[] = [];
  private counters: Record<string, number> = {};
  private eCount = 0;
  add(type: string, name: string, o: Partial<VNode> & { evidence?: Evidence[] } = {}): VNode {
    const p = PREFIX[type] ?? "NODE";
    const n = (this.counters[p] = (this.counters[p] ?? 0) + 1);
    const node: VNode = {
      id: `${p}-${String(n).padStart(4, "0")}`,
      type, name, module: null, qualified_symbol: null, responsibility: null,
      evidence: [], quirk: false, suspected_bug: false, confidence: "low", since: 1,
      ...o,
    };
    this.nodes.push(node);
    return node;
  }
  link(source: string, target: string, type: string, o: Partial<VEdge> = {}) {
    this.eCount++;
    this.edges.push({
      id: `EDGE-${String(this.eCount).padStart(4, "0")}`, source, target, type,
      evidence: [], confidence: "low", since: 1, ...o,
    });
  }
}

const ev = (file: string, a: number, b: number | null, quote: string, symbol?: string): Evidence => ({
  file, line_start: a, line_end: b, quote, symbol: symbol ?? null,
});

/* ----------------------------------------------------------------------------------------------- petclinic */

const PC = "src/main/java/org/springframework/samples/petclinic";

export function buildPetclinic(): FixtureGraph {
  const b = new Builder();
  const R = rng(7);
  const owner = b.add("data_entity", "Owner", {
    module: `${PC}/owner/Owner.java`, qualified_symbol: "org.springframework.samples.petclinic.owner.Owner",
    responsibility: "A pet owner. Holds contact details and the list of pets belonging to the owner; pets are sorted by name when read.",
    evidence: [ev(`${PC}/owner/Owner.java`, 47, 58, "@Column(name = \"address\")\n@NotBlank\nprivate String address;\n\n@Column(name = \"city\")\n@NotBlank\nprivate String city;\n\n@Column(name = \"telephone\")\n@NotBlank\n@Pattern(regexp = \"\\\\d{10}\", message = \"{telephone.invalid}\")")],
    confidence: "medium",
  });
  const pet = b.add("data_entity", "Pet", {
    module: `${PC}/owner/Pet.java`, responsibility: "A pet with a birth date and type; visits are stored per pet and kept ordered by date.",
    evidence: [ev(`${PC}/owner/Pet.java`, 40, 52, "@Column(name = \"birth_date\")\n@DateTimeFormat(pattern = \"yyyy-MM-dd\")\nprivate LocalDate birthDate;\n\n@ManyToOne\n@JoinColumn(name = \"type_id\")\nprivate PetType type;")],
    confidence: "medium",
  });
  const petType = b.add("data_entity", "PetType", {
    module: `${PC}/owner/PetType.java`, responsibility: "Lookup entity naming the species a pet belongs to (cat, dog, hamster ...).",
    evidence: [ev(`${PC}/owner/PetType.java`, 28, 32, "@Entity\n@Table(name = \"types\")\npublic class PetType extends NamedEntity {")],
  });
  const visit = b.add("data_entity", "Visit", {
    module: `${PC}/owner/Visit.java`, responsibility: "A clinic visit for a pet with a date (defaults to today) and a free-text description.",
    evidence: [ev(`${PC}/owner/Visit.java`, 36, 44, "@Column(name = \"visit_date\")\n@DateTimeFormat(pattern = \"yyyy-MM-dd\")\nprivate LocalDate date;\n\n@NotBlank\nprivate String description;")],
    confidence: "medium",
  });
  const vet = b.add("data_entity", "Vet", {
    module: `${PC}/vet/Vet.java`, responsibility: "A veterinarian with a set of specialties, exposed sorted by name.",
    evidence: [ev(`${PC}/vet/Vet.java`, 42, 50, "@ManyToMany(fetch = FetchType.EAGER)\n@JoinTable(name = \"vet_specialties\", joinColumns = @JoinColumn(name = \"vet_id\"),\ninverseJoinColumns = @JoinColumn(name = \"specialty_id\"))\nprivate Set<Specialty> specialties;")],
  });
  const spec = b.add("data_entity", "Specialty", {
    module: `${PC}/vet/Specialty.java`, responsibility: "A named veterinary specialty such as radiology or surgery.",
    evidence: [ev(`${PC}/vet/Specialty.java`, 27, 30, "@Entity\n@Table(name = \"specialties\")\npublic class Specialty extends NamedEntity {")],
  });
  b.add("data_entity", "Person", {
    module: `${PC}/model/Person.java`, responsibility: "Mapped superclass that gives Owner and Vet a first and last name.",
    evidence: [ev(`${PC}/model/Person.java`, 30, 38, "@MappedSuperclass\npublic class Person extends BaseEntity {\n\t@Column(name = \"first_name\")\n\t@NotBlank\n\tprivate String firstName;")],
  });

  const ownerRepo = b.add("component", "OwnerRepository", {
    module: `${PC}/owner/OwnerRepository.java`,
    responsibility: "Spring Data repository for owners. Finds owners by last name prefix with paging and loads an owner with eager pets.",
    evidence: [ev(`${PC}/owner/OwnerRepository.java`, 38, 46, "Page<Owner> findByLastNameStartingWith(String lastName, Pageable pageable);\n\nOptional<Owner> findById(Integer id);")],
    confidence: "medium",
  });
  const vetRepo = b.add("component", "VetRepository", {
    module: `${PC}/vet/VetRepository.java`, responsibility: "Read-only repository returning vets with their specialties; results are cached under the \"vets\" cache.",
    evidence: [ev(`${PC}/vet/VetRepository.java`, 36, 42, "@Transactional(readOnly = true)\n@Cacheable(\"vets\")\nCollection<Vet> findAll() throws DataAccessException;")],
    confidence: "medium",
  });
  const ownerCtl = b.add("component", "OwnerController", {
    module: `${PC}/owner/OwnerController.java`, responsibility: "Web controller for finding, creating and editing owners and showing an owner with their pets and visits.",
    evidence: [ev(`${PC}/owner/OwnerController.java`, 48, 56, "@Controller\nclass OwnerController {\n\n\tprivate static final String VIEWS_OWNER_CREATE_OR_UPDATE_FORM = \"owners/createOrUpdateOwnerForm\";")],
    confidence: "medium",
  });
  const petCtl = b.add("component", "PetController", {
    module: `${PC}/owner/PetController.java`, responsibility: "Handles adding and editing pets for an owner and validates name uniqueness and birth date.",
    evidence: [ev(`${PC}/owner/PetController.java`, 46, 52, "@Controller\n@RequestMapping(\"/owners/{ownerId}\")\nclass PetController {")],
    confidence: "medium",
  });
  const visitCtl = b.add("component", "VisitController", {
    module: `${PC}/owner/VisitController.java`, responsibility: "Creates new visits for a pet and redirects to the owner page.",
    evidence: [ev(`${PC}/owner/VisitController.java`, 40, 44, "@Controller\nclass VisitController {")],
  });
  const vetCtl = b.add("component", "VetController", {
    module: `${PC}/vet/VetController.java`, responsibility: "Lists vets as an HTML page (paged) and as JSON/XML for API clients.",
    evidence: [ev(`${PC}/vet/VetController.java`, 38, 44, "@Controller\nclass VetController {\n\n\tprivate final VetRepository vetRepository;")],
    confidence: "medium",
  });
  const validator = b.add("component", "PetValidator", {
    module: `${PC}/owner/PetValidator.java`, responsibility: "Validates pet name, type (new pets) and birth date; supplies required-field messages.",
    evidence: [ev(`${PC}/owner/PetValidator.java`, 40, 58, "if (!StringUtils.hasText(name)) {\n\terrors.rejectValue(\"name\", REQUIRED, REQUIRED);\n}\n\nif (pet.isNew() && pet.getType() == null) {\n\terrors.rejectValue(\"type\", REQUIRED, REQUIRED);\n}")],
    confidence: "medium",
  });
  const formatter = b.add("component", "PetTypeFormatter", {
    module: `${PC}/owner/PetTypeFormatter.java`, responsibility: "Converts between PetType objects and their display names for form binding.",
    evidence: [ev(`${PC}/owner/PetTypeFormatter.java`, 36, 50, "public PetType parse(String text, Locale locale) throws ParseException {\n\tCollection<PetType> findPetTypes = this.types.findPetTypes();\n\tfor (PetType type : findPetTypes) {\n\t\tif (type.getName().equals(text)) {\n\t\t\treturn type;")],
  });
  const cacheCfg = b.add("component", "CacheConfiguration", {
    module: `${PC}/system/CacheConfiguration.java`, responsibility: "Registers the JCache \"vets\" cache with statistics enabled.",
    evidence: [ev(`${PC}/system/CacheConfiguration.java`, 33, 40, "@Configuration(proxyBeanMethods = false)\n@EnableCaching\nclass CacheConfiguration {")],
    confidence: "medium",
  });
  const crash = b.add("component", "CrashController", {
    module: `${PC}/system/CrashController.java`, responsibility: "Throws a RuntimeException on /oups to demonstrate the error page.",
    evidence: [ev(`${PC}/system/CrashController.java`, 30, 38, "@GetMapping(\"/oups\")\npublic String triggerException() {\n\tthrow new RuntimeException(\"Expected: controller used to showcase what happens when an exception is thrown\");")],
    quirk: true,
  });
  const app = b.add("component", "PetClinicApplication", {
    module: `${PC}/PetClinicApplication.java`, responsibility: "Spring Boot entry point with runtime hints for the native build.",
    evidence: [ev(`${PC}/PetClinicApplication.java`, 28, 36, "@SpringBootApplication\n@ImportRuntimeHints(PetClinicRuntimeHints.class)\npublic class PetClinicApplication {")],
    confidence: "medium",
  });

  const eps: Array<[string, string, typeof ownerCtl, string, Confidence?]> = [
    ["GET /owners/find", "Show the owner search form.", ownerCtl, "@GetMapping(\"/owners/find\")\npublic String initFindForm() {"],
    ["GET /owners", "Search owners by last name prefix; redirects to the owner page when exactly one matches, 10 per page.", ownerCtl, "@GetMapping(\"/owners\")\npublic String processFindForm(@RequestParam(defaultValue = \"1\") int page, Owner owner, BindingResult result, Model model) {", "medium"],
    ["GET /owners/new", "Show the new owner form.", ownerCtl, "@GetMapping(\"/owners/new\")\npublic String initCreationForm() {"],
    ["POST /owners/new", "Create an owner; field errors re-render the form.", ownerCtl, "@PostMapping(\"/owners/new\")\npublic String processCreationForm(@Valid Owner owner, BindingResult result, RedirectAttributes redirectAttributes) {", "medium"],
    ["GET /owners/{ownerId}", "Show an owner with pets and visits.", ownerCtl, "@GetMapping(\"/owners/{ownerId}\")\npublic ModelAndView showOwner(@PathVariable(\"ownerId\") int ownerId) {"],
    ["GET /owners/{ownerId}/edit", "Show the edit owner form.", ownerCtl, "@GetMapping(\"/owners/{ownerId}/edit\")\npublic String initUpdateOwnerForm() {"],
    ["POST /owners/{ownerId}/edit", "Update an owner; rejects when the path id and body id differ.", ownerCtl, "@PostMapping(\"/owners/{ownerId}/edit\")\npublic String processUpdateOwnerForm(@Valid Owner owner, BindingResult result, @PathVariable(\"ownerId\") int ownerId,", "medium"],
    ["GET /owners/{ownerId}/pets/new", "Show the add pet form.", petCtl, "@GetMapping(\"/pets/new\")\npublic String initCreationForm(Owner owner, ModelMap model) {"],
    ["POST /owners/{ownerId}/pets/new", "Add a pet to the owner; rejects duplicate names and future birth dates.", petCtl, "@PostMapping(\"/pets/new\")\npublic String processCreationForm(Owner owner, @Valid Pet pet, BindingResult result,", "medium"],
    ["GET /owners/{ownerId}/pets/{petId}/edit", "Show the edit pet form.", petCtl, "@GetMapping(\"/pets/{petId}/edit\")\npublic String initUpdateForm() {"],
    ["POST /owners/{ownerId}/pets/{petId}/edit", "Update a pet and keep the same-name check scoped to other pets.", petCtl, "@PostMapping(\"/pets/{petId}/edit\")\npublic String processUpdateForm(Owner owner, @Valid Pet pet, BindingResult result,"],
    ["GET /owners/{ownerId}/pets/{petId}/visits/new", "Show the new visit form.", visitCtl, "@GetMapping(\"/owners/{ownerId}/pets/{petId}/visits/new\")\npublic String initNewVisitForm() {"],
    ["POST /owners/{ownerId}/pets/{petId}/visits/new", "Add a visit to a pet.", visitCtl, "@PostMapping(\"/owners/{ownerId}/pets/{petId}/visits/new\")\npublic String processNewVisitForm(@ModelAttribute Owner owner, @PathVariable int petId, @Valid Visit visit,"],
    ["GET /vets.html", "Paged list of vets with specialties.", vetCtl, "@GetMapping(\"/vets.html\")\npublic String showVetList(@RequestParam(defaultValue = \"1\") int page, Model model) {", "medium"],
    ["GET /vets", "All vets as JSON or XML.", vetCtl, "@GetMapping({ \"/vets\" })\npublic @ResponseBody Vets showResourcesVetList() {"],
    ["GET /oups", "Deliberately throws to demonstrate the error page.", crash, "@GetMapping(\"/oups\")"],
    ["GET /", "Home page.", ownerCtl, "@GetMapping(\"/\")\npublic String welcome() {"],
    ["GET /error", "Generic error view.", crash, "<error>/error</error>"],
  ];
  eps.forEach(([name, resp, ctl, quote, conf], i) => {
    const e = b.add("endpoint", name, {
      module: ctl.module, responsibility: resp, evidence: [ev(ctl.module ?? "", 50 + i * 3, 52 + i * 3, quote)], confidence: conf ?? "low",
    });
    b.link(ctl.id, e.id, "exposes", { evidence: e.evidence });
  });

  b.link(ownerCtl.id, ownerRepo.id, "depends_on", { confidence: "medium", evidence: [ev(ctl(ownerCtl), 51, null, "private final OwnerRepository owners;")] });
  b.link(petCtl.id, ownerRepo.id, "depends_on", { confidence: "medium", evidence: [ev(ctl(petCtl), 54, null, "private final OwnerRepository owners;")] });
  b.link(visitCtl.id, ownerRepo.id, "depends_on", { evidence: [ev(ctl(visitCtl), 46, null, "private final OwnerRepository owners;")] });
  b.link(vetCtl.id, vetRepo.id, "depends_on", { confidence: "medium", evidence: [ev(ctl(vetCtl), 40, null, "private final VetRepository vetRepository;")] });
  b.link(petCtl.id, validator.id, "calls", { evidence: [ev(ctl(petCtl), 60, null, "dataBinder.setValidator(new PetValidator());")] });
  b.link(petCtl.id, formatter.id, "depends_on", { evidence: [] });
  b.link(ownerRepo.id, owner.id, "persists", { confidence: "medium" });
  b.link(vetRepo.id, vet.id, "persists", { confidence: "medium" });
  b.link(vetRepo.id, cacheCfg.id, "reads", { evidence: [ev(ctl(vetRepo), 37, null, "@Cacheable(\"vets\")")] });
  b.link(owner.id, pet.id, "owns", { confidence: "medium" });
  b.link(pet.id, visit.id, "owns", { confidence: "medium" });
  b.link(pet.id, petType.id, "references");
  b.link(vet.id, spec.id, "references");
  b.link(app.id, cacheCfg.id, "depends_on");
  b.link(visitCtl.id, visit.id, "calls");

  const rules: Array<[string, string, string, string, number, Confidence?, boolean?]> = [
    ["Owner telephone must be ten digits", "Owner telephone numbers are validated with a ten-digit pattern; any other format produces the telephone.invalid message.", "owner/Owner.java", "@Pattern(regexp = \"\\\\d{10}\", message = \"{telephone.invalid}\")", 57, "medium"],
    ["Pet names unique per owner", "A pet cannot be added when the owner already has a pet with the same name (case-insensitive) unless it is the same pet being edited.", "owner/PetController.java", "Pet existingPet = owner.getPet(pet.getName(), false);\nif (existingPet != null && !Objects.equals(pet.getId(), existingPet.getId())) {\n\tresult.rejectValue(\"name\", \"duplicate\", \"already exists\");", 88, "medium"],
    ["Pet birth date cannot be in the future", "A pet whose birth date is after today is rejected on create and update.", "owner/PetController.java", "if (pet.getBirthDate() != null && pet.getBirthDate().isAfter(currentDate)) {\n\tresult.rejectValue(\"birthDate\", \"typeMismatch.birthDate\");", 96, "medium"],
    ["Owner search pages ten at a time", "Owner search results are paged with a fixed page size of 5; a single match skips the list and opens the owner.", "owner/OwnerController.java", "int pageSize = 5;\nPageable pageable = PageRequest.of(page - 1, pageSize);", 122, "low"],
    ["Edit rejects mismatched owner id", "Updating an owner with an id in the body that differs from the path variable is rejected with an error message and the form is shown again.", "owner/OwnerController.java", "if (!Objects.equals(owner.getId(), ownerId)) {\n\tresult.rejectValue(\"id\", \"mismatch\", \"The owner ID in the form does not match the URL.\");", 131, "medium"],
    ["Pets sorted by name", "Pets of an owner are always returned in ascending name order, case-insensitive.", "owner/Owner.java", "Collections.sort(sortedPets, new PropertyComparator<>(\"name\", true, true));", 97, "low"],
    ["Vets sorted by name with specialties", "Specialties of a vet are returned sorted by name; vets without specialties show \"none\".", "vet/Vet.java", "PropertyComparator.sort(sortedSpecs, new MutableSortDefinition(\"name\", true, true));", 61, "low"],
    ["New visits default to today", "A visit created without a date is dated today.", "owner/Visit.java", "public Visit() {\n\tthis.date = LocalDate.now();\n}", 38, "low"],
    ["Visit description required", "A visit cannot be saved without a non-blank description.", "owner/Visit.java", "@NotBlank\nprivate String description;", 43, "low"],
    ["Pet type required for new pets", "Pets that have not been saved yet must have a type; existing pets are exempt.", "owner/PetValidator.java", "if (pet.isNew() && pet.getType() == null) {", 50, "low", true],
  ];
  rules.forEach(([name, resp, file, quote, line, conf]) => {
    const r = b.add("business_rule", name, {
      module: `${PC}/${file}`, responsibility: resp, evidence: [ev(`${PC}/${file}`, line, line + quote.split("\n").length - 1, quote)], confidence: conf ?? "low",
      suspected_bug: name.startsWith("Owner search") ,
    });
    const host = b.nodes.find((n) => n.module === `${PC}/${file}` && n.type !== "business_rule");
    if (host) b.link(host.id, r.id, "enforces", { evidence: r.evidence });
  });
  const ownerSearchRule = b.nodes.find((n) => n.name.startsWith("Owner search"));
  if (ownerSearchRule) ownerSearchRule.name = "Owner search is paged";

  const ARTIFACTS = [
    "org.springframework.boot:spring-boot-starter-web", "org.springframework.boot:spring-boot-starter-data-jpa", "org.springframework.boot:spring-boot-starter-thymeleaf",
    "org.springframework.boot:spring-boot-starter-validation", "org.springframework.boot:spring-boot-starter-cache", "org.springframework.boot:spring-boot-starter-actuator",
    "org.springframework.boot:spring-boot-starter-test", "org.springframework.boot:spring-boot-devtools", "org.springframework.boot:spring-boot-docker-compose",
    "org.springframework.boot:spring-boot-testcontainers", "javax.cache:cache-api", "org.ehcache:ehcache", "com.h2database:h2", "com.mysql:mysql-connector-j",
    "org.postgresql:postgresql", "org.webjars:webjars-locator-lite", "org.webjars.npm:bootstrap", "org.webjars.npm:font-awesome", "jakarta.xml.bind:jakarta.xml.bind-api",
    "org.hibernate.orm:hibernate-core", "org.hibernate.validator:hibernate-validator", "org.testcontainers:mysql", "org.testcontainers:postgresql", "org.testcontainers:junit-jupiter",
    "org.junit.jupiter:junit-jupiter", "org.mockito:mockito-core", "org.assertj:assertj-core", "io.micrometer:micrometer-registry-prometheus", "org.apache.maven.plugins:maven-compiler-plugin",
    "org.apache.maven.plugins:maven-surefire-plugin", "org.graalvm.buildtools:native-maven-plugin", "org.springframework.boot:spring-boot-maven-plugin", "io.spring.javaformat:spring-javaformat-maven-plugin",
    "org.jacoco:jacoco-maven-plugin", "org.cyclonedx:cyclonedx-maven-plugin", "org.apache.maven.plugins:maven-checkstyle-plugin", "com.puppycrawl.tools:checkstyle",
    "org.springframework:spring-webmvc", "org.springframework:spring-orm", "org.springframework:spring-context-support", "org.thymeleaf:thymeleaf-spring6", "org.slf4j:slf4j-api",
    "ch.qos.logback:logback-classic", "com.fasterxml.jackson.core:jackson-databind", "com.zaxxer:HikariCP", "org.yaml:snakeyaml", "org.apache.tomcat.embed:tomcat-embed-core",
    "net.bytebuddy:byte-buddy", "org.glassfish.jaxb:jaxb-runtime", "org.xmlunit:xmlunit-core",
  ];
  const deps = ARTIFACTS.map((a, i) => {
    const d = b.add("external_dependency", a, { module: "pom.xml", responsibility: `Maven dependency ${a}.`, evidence: [ev("pom.xml", 40 + i * 6, 44 + i * 6, `<dependency>\n  <groupId>${a.split(":")[0]}</groupId>\n  <artifactId>${a.split(":")[1]}</artifactId>`)] });
    return d;
  });
  [ownerRepo, vetRepo].forEach((r) => b.link(r.id, deps[1]!.id, "depends_on"));
  b.link(ownerCtl.id, deps[0]!.id, "depends_on"); b.link(ownerCtl.id, deps[2]!.id, "depends_on");
  b.link(cacheCfg.id, deps[10]!.id, "depends_on"); b.link(cacheCfg.id, deps[11]!.id, "depends_on");

  const FILLER = ["BaseEntity", "NamedEntity", "PetClinicRuntimeHints", "WebConfiguration", "WelcomeController", "PetTypeRepository", "SpecialtyRepository", "Vets", "OwnerFormatter", "OwnerValidator", "PostgresIntegrationTests", "MysqlTestApplication", "MySqlIntegrationTests", "PetClinicIntegrationTests", "OwnerControllerTests", "PetControllerTests", "VisitControllerTests", "VetControllerTests", "CrashControllerTests", "PetValidatorTests", "PetTypeFormatterTests", "VetTests", "ValidatorTests", "I18nPropertiesSyncTest", "ClinicServiceTests", "PetClinicApplicationTests", "MavenWrapperDownloader", "SpringRuntimeHints", "ApplicationTests", "HomeControllerTests", "ErrorViewTests", "CacheConfigTests", "GraalVmHints", "BuildInfoContributor", "JpaMappingTests", "VetsXmlTests", "OwnerBuilder", "PetBuilder", "VisitBuilder", "ThemeSwitcherController"];
  FILLER.forEach((nm, i) => {
    const test = /Test/.test(nm);
    b.add("component", nm, {
      module: `${test ? "src/test/java" : PC}/${nm}.java`, responsibility: i % 5 === 0 ? `Supporting class ${nm}.` : null, confidence: "low",
      evidence: [ev(`${test ? "src/test/java" : PC}/${nm}.java`, 24, 24, `class ${nm} {`)],
    });
  });

  const LOCALES = ["", "_de", "_es", "_fa", "_ko", "_pt", "_ru", "_tr", "_en", "_it", "_fr", "_nl", "_pl", "_ja"];
  const KEYS = ["welcome", "required", "notFound", "duplicate", "nonNumeric", "duplicateFormSubmission", "typeMismatch.date", "typeMismatch.birthDate", "owner", "owners", "firstName", "lastName", "address", "city", "telephone", "ownerInformation", "findOwner", "findOwners", "addOwner", "editOwner", "addPet", "editPet", "newPet", "pet", "pets", "petsAndVisits", "birthDate", "type", "name", "visit", "visits", "newVisit", "addVisit", "previousVisits", "date", "description", "veterinarians", "specialties", "none", "error", "home", "update", "submit", "search", "next", "previous", "telephone.invalid", "somethingHappened", "ok"];
  let cfgN = 0;
  for (const loc of LOCALES) for (const k of KEYS) {
    if (cfgN >= 688) break;
    cfgN++;
    b.add("config", `messages${loc}.properties: ${k}`, { module: `src/main/resources/messages/messages${loc}.properties`, evidence: [ev(`src/main/resources/messages/messages${loc}.properties`, cfgN % 60 + 1, null, `${k}=${k.replace(/\./g, " ")}`)] });
  }
  while (cfgN < 688) { cfgN++; b.add("config", `spring.setting.${cfgN}`, { module: "src/main/resources/application.properties", evidence: [ev("src/main/resources/application.properties", cfgN % 40 + 1, null, `spring.setting.${cfgN}=value`)] }); }
  const cfgs = b.nodes.filter((n) => n.type === "config");
  for (let i = 0; i < 40; i++) b.link(R() < 0.5 ? ownerCtl.id : petCtl.id, cfgs[Math.floor(R() * cfgs.length)]!.id, "reads");

  // Versioning: v1 = first analysis (facts + small model layer), v2 = after reanalysis, v3 = latest.
  b.nodes.forEach((n, i) => {
    if (n.type === "business_rule") n.since = i % 3 === 0 ? 2 : 1;
    if (n.name === "CrashController") n.changedIn = [3];
    if (n.name === "Pet type required for new pets") n.since = 3;
    if (n.name === "PetTypeFormatter") n.until = 3;
    if (n.name === "VetController" || n.name === "OwnerRepository") n.changedIn = [2];
  });
  b.nodes.find((n) => n.name === "CrashController")!.confidence = "medium";
  const retired = b.add("component", "WelcomeConfig", { module: `${PC}/system/WelcomeConfig.java`, responsibility: "Removed in the latest source: welcome page now served by WelcomeController.", evidence: [ev(`${PC}/system/WelcomeConfig.java`, 22, 24, "class WelcomeConfig {")], since: 1, until: 2 });
  void retired;
  b.edges.forEach((e) => {
    const s = b.nodes.find((n) => n.id === e.source), t = b.nodes.find((n) => n.id === e.target);
    e.since = Math.max(s?.since ?? 1, t?.since ?? 1);
    const u = Math.min(s?.until ?? 99, t?.until ?? 99);
    if (u < 99) e.until = u;
  });
  return { nodes: b.nodes, edges: b.edges, versions: [1, 2, 3] };
}

function ctl(n: GraphNode): string {
  return n.module ?? "";
}

export const PETCLINIC_FINDINGS: Finding[] = [
  {
    id: "FIND-0001", severity: "high", title: "Docs say owner search is paged 10 at a time; code pages 5 at a time",
    detail: "The README states that owner search results are shown ten per page. The controller hard-codes a page size of 5.",
    quotes: [{ text: "Search results for owners are displayed 10 per page.", source: "docs/user-guide.md:41" }, { text: "int pageSize = 5;", source: `${PC}/owner/OwnerController.java:122` }],
    item_ids: ["RULE-0004"], resolution: null,
  },
  {
    id: "FIND-0002", severity: "high", title: "Telephone validation rejects international numbers",
    detail: "The requirement says telephone numbers may include an international prefix. The validation pattern accepts exactly ten digits.",
    quotes: [{ text: "Telephone numbers may include a country code.", source: "docs/requirements.md:18" }, { text: "@Pattern(regexp = \"\\\\d{10}\", message = \"{telephone.invalid}\")", source: `${PC}/owner/Owner.java:57` }],
    item_ids: ["RULE-0001", "ENT-0001"], resolution: null,
  },
  {
    id: "FIND-0003", severity: "medium", title: "Vets list is cached but the docs describe it as always fresh",
    detail: "VetRepository.findAll is cached; a newly added vet will not show until the cache expires.",
    quotes: [{ text: "The vets page always shows the current list of veterinarians.", source: "docs/user-guide.md:77" }, { text: "@Cacheable(\"vets\")", source: `${PC}/vet/VetRepository.java:37` }],
    item_ids: ["COMP-0002", "COMP-0006"], resolution: { status: "accepted_doc", note: "The generated document is right: the vets list is cached. Update the user guide.", author: "maya@acme.test", at: "2026-11-08T14:21:00Z" },
  },
  {
    id: "FIND-0004", severity: "medium", title: "A pet's type is only required when the pet is new",
    detail: "The docs say every pet must have a type. The validator skips the check on edit.",
    quotes: [{ text: "Every pet must have a type.", source: "docs/requirements.md:24" }, { text: "if (pet.isNew() && pet.getType() == null) {", source: `${PC}/owner/PetValidator.java:50` }],
    item_ids: ["RULE-0010"], resolution: null,
  },
  {
    id: "FIND-0005", severity: "low", title: "The /oups endpoint always throws",
    detail: "An endpoint exists purely to demonstrate error handling. It is not mentioned in the documentation.",
    quotes: [{ text: "throw new RuntimeException(\"Expected: controller used to showcase what happens when an exception is thrown\");", source: `${PC}/system/CrashController.java:36` }],
    item_ids: ["EP-0016", "COMP-0010"], resolution: { status: "dismissed", note: "Intentional demo endpoint.", author: "dev@acme.test", at: "2026-11-08T15:02:00Z" },
  },
  {
    id: "FIND-0006", severity: "low", title: "Owner id mismatch message is not localised",
    detail: "The mismatch error uses a hard-coded English string while other validation errors use message keys.",
    quotes: [{ text: "result.rejectValue(\"id\", \"mismatch\", \"The owner ID in the form does not match the URL.\");", source: `${PC}/owner/OwnerController.java:131` }],
    item_ids: ["RULE-0005"], resolution: null,
  },
];

/* ----------------------------------------------------------------------------------------------- django */

export function buildDjango(): FixtureGraph {
  const b = new Builder();
  const mk = (type: string, name: string, file: string, resp: string, quote: string, line: number, conf: Confidence = "low") =>
    b.add(type, name, { module: file, responsibility: resp, evidence: [ev(file, line, line + quote.split("\n").length - 1, quote)], confidence: conf });
  const workshop = mk("data_entity", "Workshop", "library/models.py", "A scheduled workshop with a limited number of seats.", "class Workshop(models.Model):\n    title = models.CharField(max_length=200)\n    seats = models.PositiveIntegerField()", 8, "medium");
  const reg = mk("data_entity", "Registration", "library/models.py", "A person's registration for a workshop, optionally on the waitlist.", "class Registration(models.Model):\n    workshop = models.ForeignKey(Workshop, related_name=\"registrations\")\n    waitlisted = models.BooleanField(default=False)", 18, "medium");
  const cfg = mk("config", "MAX_WORKSHOP_SEATS", "settings.py", "Upper bound for seats in a single workshop.", "MAX_WORKSHOP_SEATS = 40", 31, "medium");
  const svc = mk("component", "library/services.py", "library/services.py", "Registration and cancellation logic including waitlist handling.", "def register(workshop, person):\n    if workshop.seats > settings.MAX_WORKSHOP_SEATS:", 5, "medium");
  const views = mk("component", "library/views.py", "library/views.py", "Django REST views for workshops and registrations.", "class WorkshopViewSet(viewsets.ModelViewSet):", 12);
  const e1 = mk("endpoint", "GET /api/workshops/", "library/urls.py", "List workshops.", "router.register(r'workshops', WorkshopViewSet)", 6);
  const e2 = mk("endpoint", "POST /api/registrations/", "library/urls.py", "Register for a workshop; goes to the waitlist when full.", "router.register(r'registrations', RegistrationViewSet)", 7, "medium");
  const rule = mk("business_rule", "Waitlist Promotion", "library/services.py", "When a non-waitlisted registration is cancelled, the first waitlisted person for that workshop is moved to active status.", "if not registration.waitlisted:\n    nxt = workshop.registrations.filter(waitlisted=True).first()\n    if nxt:\n        nxt.waitlisted = False\n        nxt.save()", 18, "medium");
  const rule2 = mk("business_rule", "Seat cap enforced at registration", "library/services.py", "A workshop cannot be created with more seats than MAX_WORKSHOP_SEATS.", "if workshop.seats > settings.MAX_WORKSHOP_SEATS:\n    raise ValidationError('too many seats')", 9, "high");
  const rule3 = mk("business_rule", "Duplicate registration blocked", "library/services.py", "A person cannot register twice for the same workshop.", "if Registration.objects.filter(workshop=workshop, person=person).exists():\n    raise ValidationError('already registered')", 12, "high");
  b.link(views.id, svc.id, "calls", { confidence: "medium" }); b.link(svc.id, workshop.id, "depends_on"); b.link(svc.id, reg.id, "depends_on");
  b.link(svc.id, cfg.id, "reads", { confidence: "medium" }); b.link(views.id, e1.id, "exposes"); b.link(views.id, e2.id, "exposes");
  b.link(svc.id, rule.id, "enforces"); b.link(svc.id, rule2.id, "enforces", { confidence: "high" }); b.link(svc.id, rule3.id, "enforces", { confidence: "high" });
  ["models.py", "admin.py", "apps.py", "tests.py", "migrations/0001_initial.py", "urls.py", "serializers.py"].forEach((f) => {
    const c = mk("component", `library/${f}`, `library/${f}`, "", `# ${f}`, 1);
    c.responsibility = "(facts only: not yet analysed)";
  });
  ["django", "djangorestframework", "psycopg2-binary", "gunicorn", "whitenoise"].forEach((d, i) => mk("external_dependency", d, "requirements.txt", `Python dependency ${d}.`, d, i + 1));
  return { nodes: b.nodes, edges: b.edges, versions: [1, 2, 3, 4] };
}

export const DJANGO_FINDINGS: Finding[] = [
  {
    id: "FIND-0001", severity: "high", title: "Docs promise e-mail on waitlist promotion; no e-mail is sent",
    detail: "The README says the promoted person is notified by e-mail. The promotion code only flips the waitlisted flag.",
    quotes: [{ text: "When a seat opens the next person on the waitlist is notified by e-mail.", source: "README.md:22" }, { text: "nxt.waitlisted = False\nnxt.save()", source: "library/services.py:21" }],
    item_ids: ["RULE-0001"], resolution: null,
  },
];

/* ----------------------------------------------------------------------------------------------- large synthetic */

export function buildLedger(total = 5200): FixtureGraph {
  const b = new Builder();
  const R = rng(99);
  const pick = <T,>(a: readonly T[]) => a[Math.floor(R() * a.length)]!;
  const MODS = ["billing", "invoicing", "ledger", "tax", "payments", "reconciliation", "dunning", "fx", "reporting", "customers", "subscriptions", "audit", "notifications", "export", "pricing", "credits"];
  const SUF = ["Service", "Repository", "Controller", "Mapper", "Validator", "Job", "Client", "Calculator", "Publisher", "Handler", "Policy", "Factory"];
  const NOUN = ["Invoice", "Payment", "Ledger", "Account", "Tax", "Refund", "Charge", "Plan", "Credit", "Statement", "Settlement", "Reminder", "Batch", "Rate", "Customer", "Entry", "Adjustment", "Period"];
  const RULES = ["is rounded to two decimals", "cannot be negative", "must be posted before period close", "requires approval above the threshold", "expires after 90 days", "is retried three times with backoff", "is applied oldest first", "must reference an open invoice", "is converted at the daily fix rate", "triggers a reminder after the grace period"];
  const targets = { component: 0.28, endpoint: 0.07, data_entity: 0.05, business_rule: 0.08, external_dependency: 0.04, config: 0.48 } as const;
  const hubs: VNode[] = [];
  const all: VNode[] = [];
  for (const [type, share] of Object.entries(targets)) {
    const cnt = Math.round(total * share);
    for (let i = 0; i < cnt; i++) {
      const mod = pick(MODS), noun = pick(NOUN), suf = pick(SUF);
      let name = "", resp: string | null = null; const file = `src/${mod}/${noun.toLowerCase()}_${suf.toLowerCase()}.py`;
      if (type === "component") { name = `${noun}${suf}${i % 7 === 0 ? "V2" : ""}`; resp = `${suf === "Service" ? "Coordinates" : "Implements"} ${noun.toLowerCase()} ${mod} logic.`; }
      else if (type === "endpoint") { name = `${pick(["GET", "POST", "PUT", "DELETE"])} /api/${mod}/${noun.toLowerCase()}s${R() < 0.5 ? "/{id}" : ""}`; resp = `${noun} operation in ${mod}.`; }
      else if (type === "data_entity") { name = `${noun}${pick(["Record", "Row", "Snapshot", "Line", ""])}${i}`; resp = `Persistent ${noun.toLowerCase()} data for ${mod}.`; }
      else if (type === "business_rule") { name = `${noun} ${pick(RULES)}`; resp = `${noun} amounts in ${mod} ${pick(RULES)}.`; }
      else if (type === "external_dependency") { name = `${pick(["stripe", "adyen", "avalara", "sap-gl", "sendgrid", "kafka", "redis", "s3", "snowflake", "vault"])}-${pick(["client", "sdk", "api", "sink"])}-${i}`; resp = "External system or library."; }
      else { name = `${mod.toUpperCase()}_${noun.toUpperCase()}_${pick(["TIMEOUT", "LIMIT", "ENABLED", "RETRIES", "URL", "BATCH_SIZE"])}_${i}`; }
      const conf: Confidence = R() < 0.07 ? "high" : R() < 0.3 ? "medium" : "low";
      const node = b.add(type, name, {
        module: file, responsibility: resp, confidence: conf, quirk: R() < 0.01, suspected_bug: type === "business_rule" && R() < 0.04,
        evidence: [ev(file, 10 + (i % 300), 14 + (i % 300), `# ${name}\ndef ${noun.toLowerCase()}_${i}(ctx):\n    return ctx.${mod}`)],
        since: R() < 0.1 ? 2 : 1,
      });
      all.push(node);
      if (type === "component" && R() < 0.1) hubs.push(node);
    }
  }
  for (const n of all) {
    if (n.type === "config" && R() < 0.5) continue;
    const links = n.type === "component" ? 2 + Math.floor(R() * 3) : 1;
    for (let k = 0; k < links; k++) {
      const t = R() < 0.35 && hubs.length ? pick(hubs) : pick(all);
      if (t.id !== n.id) b.link(n.id, t.id, pick(["calls", "depends_on", "reads", "persists", "enforces"]), { confidence: pick(["low", "low", "medium"]), since: Math.max(n.since, t.since) });
    }
  }
  return { nodes: b.nodes, edges: b.edges, versions: [1, 2] };
}
