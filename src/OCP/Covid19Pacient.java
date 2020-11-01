package OCP;
import java.util.HashMap;
import java.util.Map;

public class Covid19Pacient extends Pacient {

	public Covid19Pacient(String name, int years) {
		super(name, years);
		// TODO Auto-generated constructor stub
	}

	// Map<CardioVascularSymptom,Integer> cardios=new
	// HashMap<CardioVascularSymptom,Integer>();
	// Map<NeuroMuscularSymptom,Integer> neuros=new
	// HashMap<NeuroMuscularSymptom,Integer>();
	// Map<RespiratorySymptom,Integer> respirs=new
	// HashMap<RespiratorySymptom,Integer>();
	Map<Symptom, Integer> symptoms = new HashMap<Symptom, Integer>();

	/*
	 * public void addCardioSymptom(CardioVascularSymptom c, Integer w){
	 * cardios.put(c,w); } public void addNeuroMuscularSymptom(NeuroMuscularSymptom
	 * nm, Integer w){ neuros.put(nm,w); } public void addRespiratorySymptom
	 * (RespiratorySymptom r, Integer w){ respirs.put(r,w); }
	 */
	public void addSymptom(Symptom s, Integer w) {
		symptoms.put(s, w);

	}

	double calcCovid19Impact() {
		double afection = 0;
		double increment = 0;
		double impact;
		// calculate CalcAfection
		for (Symptom c : symptoms.keySet()) {
			
			afection = afection + c.getSeverityIndex() * symptoms.get(c);
		}
		afection = afection / (symptoms.size());
		// calculate increment
		if (getYears() > 65)
			increment = afection * 0.5;
		// calculate impact
		impact = afection + increment;
		return impact;
	}

	public double sanatedDays() {
		double max = 0;
		for (Symptom c : symptoms.keySet()) {
			if (max < c.affectedDays) {
				max = c.affectedDays;
			}
		}
		return max;
	}
}
